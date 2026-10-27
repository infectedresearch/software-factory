"""What the factory is doing, built from its log, git, and token records.

Read-only except for the explicit review, model, and start/stop helpers at
the bottom.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import config as C

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) (.*)$")
START = re.compile(r"^start model=(\S+)(?: hours=(\d+))?(?: until=(\S+))? head=(\S+)")
BEGIN = re.compile(r"^(?:iteration|pass) (\d+) begin ([0-9a-f]{7,40})(?: model=(\S+))?")
END_OLD = re.compile(r"^iteration (\d+) opencode_exit=(\d+) head=([0-9a-f]{7,40})")
END = re.compile(r"^pass (\d+) end exit=(\d+) head=([0-9a-f]{7,40})")
GATE_START = re.compile(r"^(?:iteration|pass) (\d+) (?:regression|gate start)$")
GATE_OLD = re.compile(r"^regression (passed|failed) at ([0-9a-f]+)")
GATE = re.compile(r"^pass (\d+) gate (passed|failed)(?: secs=(\S*))?")
REJECT = re.compile(r"^pass (\d+) rejected kept=(\S+)")
EMPTY = re.compile(r"^pass (\d+) no-commit")
TIMEOUT = re.compile(r"^pass (\d+) timeout")
REVIEW_START = re.compile(r"^pass (\d+) review start")
REVIEW = re.compile(r"^pass (\d+) review (keep|fix|drop|none) (\S+)")
DROPPED = re.compile(r"^pass (\d+) dropped kept=(\S+)")
SHA = re.compile(r"^[0-9a-f]{7,40}$")
FIXES = re.compile(r"^Fixes:\s*([0-9a-f]{7,40})", re.M)

_commit_cache: dict[tuple[str, str], list[dict]] = {}
_files_cache: dict[str, list[str]] = {}


def _git(repo: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _when(stamp: str | None) -> float | None:
    if not stamp:
        return None
    return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


# --------------------------------------------------------------------------
# Reading what the agent is doing
# --------------------------------------------------------------------------


def is_sentence(line: str) -> bool:
    """True for the agent's own prose, false for code, diffs, and test output."""
    if not 25 <= len(line) <= 400:
        return False
    if not re.match(r"^[A-Z\"'(]", line):
        return False
    if re.search(r"\b(Error|Traceback|Exception|AssertionError)\b", line):
        return False
    if re.search(r"[;{}]\s*$|=>|==|\)\s*\{|^\w+\(|```|^(PASS|FAIL|RUN)\b|^Index:", line):
        return False
    if len(line.split()) < 6:
        return False
    return line[-1] in ".:!?)"


def step_of(line: str) -> dict | None:
    """One tool line as a short, typed step, or None if it is not a tool line."""
    text = re.sub(r"\s+", " ", line).strip()
    if text.startswith("→ Read "):
        target = text[7:].split(" [", 1)[0]
        return {"kind": "read", "text": f"Read {target}"}
    if text.startswith("✱ Grep "):
        pattern = re.match(r'✱ Grep "(.*?)"', text)
        found = re.search(r"· (\d+) match", text)
        what = pattern.group(1)[:60] if pattern else "the code"
        tail = f" ({found.group(1)} hits)" if found else ""
        return {"kind": "search", "text": f"Searched for {what}{tail}"}
    if text.startswith("✱ Glob "):
        return {"kind": "search", "text": "Listed files " + text[7:60]}
    if text.startswith("← "):
        verb, _, target = text[2:].partition(" ")
        past = {"Edit": "Edited", "Write": "Wrote", "Patch": "Patched"}.get(verb, verb)
        return {"kind": "edit", "text": f"{past} {target}".strip()}
    if text.startswith("$ "):
        cmd = text[2:]
        if "git commit" in cmd:
            return {"kind": "commit", "text": "Committed the change"}
        if re.search(r"pytest|node --test|\.test\.js|npx playwright", cmd):
            return {"kind": "test", "text": "Ran tests: " + cmd[:90]}
        if cmd.startswith("git "):
            return {"kind": "git", "text": "Git: " + cmd[4:70]}
        return {"kind": "shell", "text": "Ran " + cmd[:90]}
    return None


def parse_log(text: str) -> dict:
    """Passes, loop runs, and the current pass's activity from the loop log."""
    passes: dict[int, dict] = {}
    runs: list[dict] = []
    events: list[dict] = []
    current: int | None = None
    sentences: list[str] = []
    steps: list[dict] = []
    in_diff = False

    for raw in ANSI.sub("", text).replace("\r", "").splitlines():
        line = raw.strip()
        if not line:
            continue
        stamped = STAMP.match(line)
        if not stamped:
            if current is None:
                continue
            if line.startswith(("←", "Index:")):
                in_diff = True
            elif line.startswith(("→", "✱", "$ ", "> ")):
                in_diff = False
            step = step_of(line)
            if step:
                steps.append(step)
            elif not in_diff and is_sentence(line):
                sentences.append(line)
            continue

        at, msg = stamped.groups()
        events.append({"at": at, "text": msg})
        if m := START.match(msg):
            runs.append({"at": at, "model": m.group(1), "hours": m.group(2), "until": m.group(3), "head": m.group(4)})
        elif m := BEGIN.match(msg):
            n = int(m.group(1))
            passes[n] = {
                "n": n,
                "before": m.group(2),
                "model": m.group(3) or (runs[-1]["model"] if runs else None),
                "begin_at": at,
                "end_at": None,
                "after": None,
                "exit": None,
                "gate": None,
                "gate_secs": None,
                "rejected": None,
                "empty": False,
                "timeout": False,
                "review": None,
                "reviews": [],
                "dropped": None,
            }
            current = n
            sentences, steps, in_diff = [], [], False
        elif (m := END.match(msg)) or (m := END_OLD.match(msg)):
            p = passes.get(int(m.group(1)))
            if p:
                p.update(end_at=at, exit=int(m.group(2)), after=m.group(3))
                p["empty"] = p["after"].startswith(p["before"][:7]) or p["before"].startswith(p["after"][:7])
        elif m := GATE_START.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["gate"] = "running"
        elif m := GATE.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["gate"] = m.group(2)
                p["gate_secs"] = m.group(3) or None
        elif m := GATE_OLD.match(msg):
            for p in sorted(passes.values(), key=lambda item: -item["n"]):
                if p["gate"] == "running":
                    p["gate"] = m.group(1)
                    break
        elif m := REJECT.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["rejected"] = m.group(2)
        elif m := EMPTY.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["empty"] = True
        elif m := TIMEOUT.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["timeout"] = True
        elif m := REVIEW_START.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["review"] = "running"
        elif m := REVIEW.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["reviews"].append({"verdict": m.group(2), "short": m.group(3)})
                p["review"] = "done"
        elif m := DROPPED.match(msg):
            p = passes.get(int(m.group(1)))
            if p:
                p["dropped"] = m.group(2)

    return {
        "passes": [passes[k] for k in sorted(passes)],
        "runs": runs,
        "events": events[-60:],
        "current": current,
        "intent": sentences[-1] if sentences else "",
        "steps": steps[-8:],
    }


def outcome(p: dict, alive: bool) -> str:
    if p["end_at"] is None:
        return "running" if alive else "interrupted"
    if p["rejected"]:
        return "rejected"
    if p.get("dropped"):
        return "dropped"
    if p["empty"]:
        return "empty"
    if p["gate"] == "running":
        return "testing" if alive else "interrupted"
    if p["gate"] == "failed":
        return "failed"
    if p.get("review") == "running" and not p.get("reviews"):
        return "reviewing" if alive else "interrupted"
    if p["gate"] == "passed":
        if any(r["verdict"] == "fix" for r in p.get("reviews", [])):
            return "needs-fix"
        return "landed"
    return "unchecked"


# --------------------------------------------------------------------------
# Tokens and commits
# --------------------------------------------------------------------------


def token_usage(worktree: Path, prefix: str, db: Path = C.USAGE_DB) -> dict[int, dict]:
    if not db.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        rows = con.execute(
            "select title, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read "
            "from session_v2 where title like ? and directory = ?",
            (prefix + "-%", str(worktree)),
        ).fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    out: dict[int, dict] = {}
    for title, inp, outp, reasoning, cache in rows:
        suffix = title.rsplit("-", 1)[-1]
        if not suffix.isdigit():
            continue
        out[int(suffix)] = {
            "prompt": int(inp or 0),
            "written": int(outp or 0),
            "thinking": int(reasoning or 0),
            "cached": int(cache or 0),
        }
    return out


def commits_between(repo: Path, before: str, after: str) -> list[dict]:
    if not before or not after or before == after:
        return []
    key = (before, after)
    if key not in _commit_cache:
        out = _git(repo, "log", "--reverse", "--format=%H%x1f%h%x1f%s", f"{before}..{after}")
        rows = []
        for line in out.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 3:
                rows.append({"sha": parts[0], "short": parts[1], "subject": parts[2]})
        _commit_cache[key] = rows
    return _commit_cache[key]


def pid_alive(path: Path) -> bool:
    try:
        os.kill(int(path.read_text().strip()), 0)
    except (OSError, ValueError):
        return False
    return True


def snapshot(lane: C.Lane, now: float | None = None) -> dict:
    now = now or datetime.now(timezone.utc).timestamp()
    try:
        text = lane.log.read_text(errors="replace")
    except OSError:
        text = ""
    parsed = parse_log(text)
    alive = pid_alive(lane.pidfile)
    tokens = token_usage(lane.worktree, lane.title)
    passes = []
    for p in parsed["passes"]:
        state = outcome(p, alive)
        began = _when(p["begin_at"])
        ended = _when(p["end_at"]) or (now if state in ("running", "testing", "reviewing") else None)
        commits = commits_between(lane.worktree, p["before"], p["after"] or "")
        passes.append({
            **p,
            "outcome": state,
            "seconds": int(ended - began) if began and ended else None,
            "commits": commits,
            "tokens": tokens.get(p["n"], {}),
        })

    run = parsed["runs"][-1] if parsed["runs"] else {}
    run_start = _when(run.get("at"))
    until = _when(run.get("until")) if run.get("until") else (
        run_start + int(run["hours"]) * 3600 if run_start and run.get("hours") else None
    )
    stop_requested = lane.stopfile.exists()
    last = passes[-1] if passes else None
    if not alive:
        phase = "stopped"
    elif last is None:
        phase = "starting"
    elif last["outcome"] == "running":
        phase = "working"
    elif last["outcome"] == "testing":
        phase = "testing"
    elif last["outcome"] == "reviewing":
        phase = "reviewing"
    else:
        phase = "between"
    if alive and stop_requested:
        phase = "stopping" if phase in ("working", "testing", "reviewing") else "stopped"

    if phase == "working":
        headline = parsed["intent"] or "Reading the code. It has not said what it will change yet."
    elif phase == "testing":
        subject = last["commits"][-1]["subject"] if last and last["commits"] else "its change"
        headline = f"Checking pass {last['n']}: {subject}"
    elif phase == "reviewing":
        subject = last["commits"][-1]["subject"] if last and last["commits"] else "its change"
        headline = f"The smart model is reviewing pass {last['n']}: {subject}"
    elif phase == "between":
        headline = "Starting the next pass."
    elif phase == "stopping":
        headline = "Finishing this pass, then stopping."
    else:
        reason = next((e["text"] for e in reversed(parsed["events"]) if e["text"].startswith(("stop file", "stalled", "done", "refusing", "worktree left"))), "")
        headline = "Stopped." + (f" Last word: {reason}." if reason else "")

    finished = [p for p in passes if p["outcome"] not in ("running", "testing", "reviewing")]
    durations = [p["seconds"] for p in finished if p["seconds"]]
    def tok_sum(field: str) -> int:
        return sum(p["tokens"].get(field, 0) for p in passes)
    counts: dict[str, int] = {}
    for p in passes:
        counts[p["outcome"]] = counts.get(p["outcome"], 0) + 1

    return {
        "lane": lane.id,
        "name": lane.name,
        "frozen": lane.frozen,
        "now": now,
        "phase": phase,
        "alive": alive,
        "stop_requested": stop_requested,
        "headline": headline,
        "steps": parsed["steps"],
        "run_started": run_start,
        "until": until,
        "current": last,
        "passes": list(reversed(passes))[:40],
        "counts": counts,
        "avg_seconds": int(sum(durations) / len(durations)) if durations else None,
        "tokens": {
            "prompt": tok_sum("prompt"),
            "written": tok_sum("written"),
            "thinking": tok_sum("thinking"),
            "cached": tok_sum("cached"),
        },
        "events": parsed["events"][-25:],
        "head": _git(lane.worktree, "rev-parse", "--short", "HEAD").stdout.strip(),
        "branch": lane.branch,
    }


def fresh_tokens(tokens: dict) -> int:
    return int(tokens.get("prompt", 0)) + int(tokens.get("thinking", 0)) + int(tokens.get("written", 0))


def overview(now: float | None = None) -> dict:
    """Every lane, plus factory-wide totals and the last hour's token burn."""
    now = now or datetime.now(timezone.utc).timestamp()
    snaps = [snapshot(item, now) for item in C.lanes()]
    counts: dict[str, int] = {}
    totals = {"prompt": 0, "written": 0, "thinking": 0, "cached": 0}
    hour = 0
    for snap in snaps:
        for key, value in snap["counts"].items():
            counts[key] = counts.get(key, 0) + value
        for key in totals:
            totals[key] += snap["tokens"][key]
        for p in snap["passes"]:
            began = _when(p["begin_at"])
            if began and began >= now - 3600:
                hour += fresh_tokens(p["tokens"])
    untils = [s["until"] for s in snaps if s["until"]]
    starts = [s["run_started"] for s in snaps if s["run_started"]]
    integration = None
    if C.INTEGRATION.is_file():
        try:
            integration = json.loads(C.INTEGRATION.read_text())
        except (OSError, json.JSONDecodeError):
            integration = None
    return {
        "integration": integration,
        "inline_review": (C.REVIEWFILE.read_text().strip() if C.REVIEWFILE.is_file() else "off") == "on",
        "reviewer": (C.REVIEW_MODELFILE.read_text().strip() if C.REVIEW_MODELFILE.is_file() else "") or C.DEFAULT_REVIEWER,
        "reviewers": list(C.REVIEWER_CHOICES),
        "now": now,
        "lanes": snaps,
        "counts": counts,
        "tokens": totals,
        "tokens_last_hour": hour,
        "running": sum(1 for s in snaps if s["alive"]),
        "until": max(untils) if untils else None,
        "run_started": min(starts) if starts else None,
        "model": (C.MODELFILE.read_text().strip() if C.MODELFILE.is_file() else "") or C.DEFAULT_MODEL,
        "models": list(C.MODEL_CHOICES),
    }


# --------------------------------------------------------------------------
# Review for master
# --------------------------------------------------------------------------


def _files(repo: Path, sha: str) -> list[str]:
    if sha not in _files_cache:
        out = _git(repo, "show", "--name-only", "--format=", sha)
        _files_cache[sha] = [line for line in out.stdout.splitlines() if line.strip()]
    return _files_cache[sha]


def _read_lines(path: Path) -> set[str]:
    try:
        return {line.strip() for line in path.read_text().splitlines() if line.strip()}
    except OSError:
        return set()


def skipped() -> set[str]:
    return _read_lines(C.SKIPFILE) | _read_lines(C.OLD_SKIPFILE)


def is_tooling(files: list[str]) -> bool:
    return bool(files) and all(f.startswith(C.TOOLING_PREFIXES) for f in files)


OVERLAY_FILES = ("browsersource3.html", "sketchpad.html", "pngtuber.html", "chat_reader.html", "overlay_utility.js", "overlay_static.py", "manager.py")


def effects(files: list[str]) -> list[dict]:
    """What including these files on master does to the running lab.

    Master in /srv/factory/target is the live source for lab services, and
    some of them serve files straight from disk. Including is a partial
    deploy for those, so say which ones.
    """
    out: list[dict] = []

    def add(level: str, text: str) -> None:
        if not any(e["text"] == text for e in out):
            out.append({"level": level, "text": text})

    for f in files:
        name = f.rsplit("/", 1)[-1]
        if f.startswith("infra/tv-streamer/") and not f.endswith((".test.js", ".spec.js")):
            add("info", "Studio edge: live only after scripts/ship.py")
        elif f.startswith("infra/landing/") and "/tests/" not in f:
            add("info", "Landing site: live only after it is deployed")
        elif f in OVERLAY_FILES or (name in OVERLAY_FILES and "/" not in f):
            add("warn", "Overlay manager serves these from disk: restart it right after including, or OBS overlays may break")
        elif f.startswith("auth/templates/") or f.startswith("auth/static/"):
            add("warn", "Auth pages read templates and static files from disk: live as soon as it is included")
        elif f.startswith("auth/") and f.endswith(".py"):
            add("info", "Auth server: live after its next restart")
        elif f.endswith(".py") and not f.startswith(("tests/", "public_api/tests/", "scripts/")):
            add("info", "Lab service code: live after that service restarts")
        elif f.startswith("infra/Caddyfile"):
            add("warn", "Caddy config: takes effect on the next Caddy reload")
    if not out and files and all(f.startswith(("tests/", "public_api/tests/")) or f.endswith((".test.js", ".spec.js", ".md")) for f in files):
        add("good", "Tests or docs only: nothing live changes")
    return out


HIGH_RISK = (
    (re.compile(r"^auth/(?!templates/|static/).*\.py$|^public_api/auth/"), "sign-in, sessions, or identity"),
    (re.compile(r"(^|/)(diag_alerts|[a-z_]*alerts?)\.py$|(^|/)scripts/[a-z_]*canary[a-z_]*\.py$"), "alerting or canaries"),
    (re.compile(r"^memory[a-z_]*\.py$|^personas/|^agent\.py$|^brain\.py$"), "agent memory or behavior"),
    (re.compile(r"^manager\.py$"), "overlay delivery"),
    (re.compile(r"^(tos_filter|chat_filter|imgur_curation)\.py$"), "content safety filters"),
    (re.compile(r"^ssrf\.py$|^rate_limit\.py$"), "network or abuse protection"),
    (re.compile(r"(^|/)migrate\.py$|(^|/)db\.py$"), "database schema or core storage"),
)
TEST_FILE = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|\.test\.[cm]?js$|\.spec\.[cm]?js$")
_numstat_cache: dict[str, dict[str, int]] = {}


def risk(repo: Path, sha: str, files: list[str]) -> dict:
    """How carefully a reviewer should read this commit."""
    if sha not in _numstat_cache:
        lines: dict[str, int] = {}
        for row in _git(repo, "show", "--numstat", "--format=", sha).stdout.splitlines():
            parts = row.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                lines[parts[2]] = int(parts[0]) + int(parts[1])
        _numstat_cache[sha] = lines
    lines = _numstat_cache[sha]
    production = {f: n for f, n in lines.items() if not TEST_FILE.search(f)}
    prod_lines = sum(production.values())
    if not production:
        return {"level": "tests", "why": "tests only", "production_lines": 0}
    reasons = sorted({why for f in production for pattern, why in HIGH_RISK if pattern.search(f)})
    if reasons:
        return {"level": "high", "why": "changes " + ", ".join(reasons), "production_lines": prod_lines}
    return {"level": "normal", "why": f"{prod_lines} production lines", "production_lines": prod_lines}


def master_checkout(repo: Path) -> Path | None:
    out = _git(repo, "worktree", "list", "--porcelain").stdout
    path = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line.split(" ", 1)[1])
        elif line.strip() == f"branch refs/heads/{C.MASTER}" and path is not None:
            return path
    return None


def pending_commits(repo: Path) -> list[dict]:
    """Commits on the branch with no patch-equivalent on master, oldest first."""
    out = _git(
        repo,
        "log",
        "--reverse",
        "--right-only",
        "--cherry-pick",
        "--no-merges",
        "--format=%H%x1f%h%x1f%s%x1f%b%x1e",
        f"{C.MASTER}...HEAD",
    )
    rows = []
    for record in out.stdout.split("\x1e"):
        parts = record.strip("\n").split("\x1f")
        if len(parts) != 4:
            continue
        sha, short, subject, body = parts
        rows.append({"sha": sha, "short": short, "subject": subject, "fixes": FIXES.findall(body)})
    return rows


def verdicts() -> dict[str, dict]:
    """Latest smart-review verdict per commit sha (see review.py)."""
    out: dict[str, dict] = {}
    if C.VERDICTS.is_file():
        for line in C.VERDICTS.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and row.get("sha"):
                out[row["sha"]] = row
    return out


def review_state(repo: Path, passes: list[dict] | None = None, lane: C.Lane | None = None) -> dict:
    done = skipped()
    judged = verdicts()
    by_pass: dict[str, int] = {}
    for p in passes or []:
        if p.get("outcome") == "landed":
            for c in p.get("commits", []):
                by_pass[c["sha"]] = p["n"]
    items, tooling, skipped_count = [], 0, 0
    for row in pending_commits(repo):
        files = _files(repo, row["sha"])
        if row["sha"] in done:
            skipped_count += 1
            continue
        if is_tooling(files):
            tooling += 1
            continue
        items.append({
            **row,
            "files": files,
            "pass": by_pass.get(row["sha"]),
            "fixed_by": [],
            "effects": effects(files),
            "risk": risk(repo, row["sha"], files),
            "verdict": judged.get(row["sha"]),
        })
    shorts = {item["short"]: item for item in items}
    for item in items:
        for target in item["fixes"]:
            for short, other in shorts.items():
                if target.startswith(short) or short.startswith(target[:7]):
                    other["fixed_by"].append(item["short"])
    checkout = master_checkout(repo)
    dirty = 0
    if checkout:
        dirty = len([l for l in _git(checkout, "status", "--porcelain").stdout.splitlines() if l.strip()])
    audit = None
    if lane is not None and lane.audit.is_file():
        try:
            audit = json.loads(lane.audit.read_text())
        except (OSError, json.JSONDecodeError):
            audit = None
    included = []
    if C.INCLUDED.is_file():
        for line in C.INCLUDED.read_text().splitlines()[-15:]:
            try:
                included.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return {
        "items": items,
        "hidden_tooling": tooling,
        "skipped": skipped_count,
        "master_checkout": str(checkout) if checkout else None,
        "master_dirty": dirty,
        "audit": audit,
        "audit_running": pid_alive(lane.audit_pid) if lane is not None else False,
        "review_running": pid_alive(lane.state / "review.pid") if lane is not None else False,
        "judged": sum(1 for item in items if item["verdict"]),
        "included": list(reversed(included)),
    }


def patch_for(repo: Path, sha: str, limit: int = 600) -> dict:
    if not SHA.match(sha or ""):
        return {"error": "Not a commit id."}
    stat = _git(repo, "show", "--stat", "--format=%B", sha)
    if stat.returncode != 0:
        return {"error": "Commit not found."}
    patch = _git(repo, "show", "--format=", "--patch", sha).stdout.splitlines()
    return {
        "message": stat.stdout.strip(),
        "patch": "\n".join(patch[:limit]),
        "clipped": len(patch) > limit,
    }


def include(repo: Path, sha: str) -> dict:
    if not SHA.match(sha or ""):
        return {"ok": False, "error": "Not a commit id."}
    queue = review_state(repo)["items"]
    if not queue:
        return {"ok": False, "error": "Nothing is waiting."}
    if queue[0]["sha"] != sha:
        return {"ok": False, "error": f"Include the older commit first: {queue[0]['short']} {queue[0]['subject']}"}
    checkout = master_checkout(repo)
    if checkout is None:
        return {"ok": False, "error": "No checkout has master."}
    import picking

    picked = picking.cherry_pick(checkout, sha)
    if not picked["ok"]:
        files = ", ".join(picked["files"][:5])
        return {"ok": False, "error": f"Master did not take it; nothing changed. {picked['error']} ({files})".strip()}
    head = _git(checkout, "rev-parse", "--short", "HEAD").stdout.strip()
    C.INCLUDED.parent.mkdir(parents=True, exist_ok=True)
    with C.INCLUDED.open("a") as handle:
        handle.write(json.dumps({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "sha": sha,
            "short": queue[0]["short"],
            "subject": queue[0]["subject"],
            "master": head,
        }) + "\n")
    return {"ok": True, "master": head}


def include_kept(repo: Path) -> dict:
    """Include the lane's waiting commits the reviewer kept, oldest first.

    Commits the reviewer dropped are skipped on the way. Stops at the first
    commit that is unreviewed, needs a fix, or does not apply to master.
    """
    included: list[str] = []
    skipped_now: list[str] = []
    stopped = ""
    error = ""
    for _ in range(500):
        queue = review_state(repo)["items"]
        if not queue:
            break
        head = queue[0]
        verdict = (head.get("verdict") or {}).get("verdict")
        if verdict == "drop":
            skip(head["sha"])
            skipped_now.append(head["short"])
            continue
        if verdict != "keep":
            why = "needs a fix; review its follow-up and include both by hand" if verdict == "fix" else "not reviewed yet"
            stopped = f"{head['short']} {head['subject']} ({why})"
            break
        result = include(repo, head["sha"])
        if not result["ok"]:
            stopped = f"{head['short']} {head['subject']}"
            error = result["error"]
            break
        included.append(result["master"])
    return {"ok": not error, "included": included, "skipped": skipped_now, "stopped_at": stopped, "error": error}


def set_inline_review(on: bool) -> dict:
    C.REVIEWFILE.parent.mkdir(parents=True, exist_ok=True)
    C.REVIEWFILE.write_text("on\n" if on else "off\n")
    return {"ok": True}


def set_reviewer(model: str) -> dict:
    if model not in C.REVIEWER_CHOICES:
        return {"ok": False, "error": "Unknown reviewer model."}
    C.REVIEW_MODELFILE.parent.mkdir(parents=True, exist_ok=True)
    C.REVIEW_MODELFILE.write_text(model + "\n")
    return {"ok": True}


def skip(sha: str) -> dict:
    if not SHA.match(sha or ""):
        return {"ok": False, "error": "Not a commit id."}
    C.SKIPFILE.parent.mkdir(parents=True, exist_ok=True)
    if sha not in _read_lines(C.SKIPFILE):
        with C.SKIPFILE.open("a") as handle:
            handle.write(sha + "\n")
    return {"ok": True}


# --------------------------------------------------------------------------
# Controls
# --------------------------------------------------------------------------


def _chosen(lane_id: str | None) -> list[C.Lane]:
    every = C.lanes()
    if not lane_id or lane_id == "all":
        return every
    return [item for item in every if item.id == lane_id]


def request_stop(lane_id: str | None = None) -> dict:
    chosen = _chosen(lane_id)
    if not chosen:
        return {"ok": False, "error": "Unknown lane."}
    for item in chosen:
        if pid_alive(item.pidfile):
            item.stopfile.parent.mkdir(parents=True, exist_ok=True)
            item.stopfile.write_text("stop\n")
    return {"ok": True}


def start_lanes(lane_id: str | None = None) -> dict:
    chosen = [item for item in _chosen(lane_id) if not item.frozen and not pid_alive(item.pidfile)]
    if not chosen:
        return {"ok": False, "error": "Already running."}
    for item in chosen:
        item.stopfile.unlink(missing_ok=True)
        result = subprocess.run(
            ["systemctl", "--user", "start", item.unit],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return {"ok": False, "error": (result.stderr or "systemctl failed").strip()[-300:]}
    return {"ok": True}


def set_model(model: str) -> dict:
    if model not in C.MODEL_CHOICES:
        return {"ok": False, "error": "Unknown model."}
    C.MODELFILE.parent.mkdir(parents=True, exist_ok=True)
    C.MODELFILE.write_text(model + "\n")
    return {"ok": True}


def start_smart_review(lane: C.Lane) -> dict:
    """Run review.py for one lane in the background (one reviewer per lane)."""
    pidfile = lane.state / "review.pid"
    if pid_alive(pidfile):
        return {"ok": False, "error": "A smart review is already running for this lane."}
    lane.state.mkdir(parents=True, exist_ok=True)
    log = (lane.state / "review.log").open("a")
    proc = subprocess.Popen(
        [sys.executable, str(C.HOME / "review.py"), "run", "--lane", lane.id],
        cwd=C.HOME,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    pidfile.write_text(str(proc.pid))

    def _reap() -> None:
        proc.wait()
        pidfile.unlink(missing_ok=True)

    threading.Thread(target=_reap, daemon=True).start()
    return {"ok": True}


def start_audit(lane: C.Lane) -> dict:
    if pid_alive(lane.audit_pid):
        return {"ok": False, "error": "An audit is already running for this lane."}
    lane.state.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "FACTORY_SCRATCH": f"{C.AUDIT_SCRATCH}-{lane.id}"}
    proc = subprocess.Popen(
        [sys.executable, str(C.HOME / "gate.py"), "--repo", str(lane.worktree),
         "--base", C.MASTER, "--out", str(lane.audit), "--audit"],
        cwd=C.HOME,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    lane.audit_pid.write_text(str(proc.pid))

    def _reap() -> None:
        # Without a wait the finished audit stays a zombie and still looks alive.
        proc.wait()
        lane.audit_pid.unlink(missing_ok=True)

    threading.Thread(target=_reap, daemon=True).start()
    return {"ok": True}
