#!/usr/bin/env python3
"""Smart-model review of the factory's commits.

    review.py packet [--lane lane-2]          write state/review/<lane>.md
    review.py run --lane lane-2 [--model M] [--limit N]

``run`` reviews each commit that master lacks and that has no verdict yet,
oldest first. Each review happens in a read-only scratch checkout of that
exact commit, so the reviewer can run tests as of that commit. Verdicts are
appended to state/verdicts.jsonl and shown on the board's Review tab.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import config as C
import status

DEFAULT_REVIEWER = "fracktron/claude-opus-5-5"
VERDICT = re.compile(r"^VERDICT\s+(\{.*\})\s*$", re.M)
PATCH_LIMIT = 900


def gate_reports(lane: C.Lane) -> dict[str, dict]:
    """Gate report per commit sha, from the pass that made it."""
    out: dict[str, dict] = {}
    snap = status.snapshot(lane)
    for p in snap["passes"]:
        path = lane.state / "gate" / f"pass-{p['n']}.json"
        if not path.is_file():
            continue
        try:
            report = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        for commit in p["commits"]:
            out[commit["sha"]] = {"pass": p["n"], **report}
    return out


def commit_brief(lane: C.Lane, item: dict, reports: dict[str, dict]) -> str:
    patch = status.patch_for(lane.worktree, item["sha"], PATCH_LIMIT)
    report = reports.get(item["sha"])
    if report:
        gate_line = (
            f"Gate: {'passed' if report.get('passed') else 'failed'} in pass {report['pass']}, "
            f"{report.get('node_files')} node test files, {len(report.get('pytest_files', []))} pytest files: "
            + ", ".join(report.get("pytest_files", [])[:20])
        )
    else:
        gate_line = "Gate: this commit was made outside the factory loop."
    effects = "; ".join(e["text"] for e in item.get("effects", [])) or "none recorded"
    risk = item.get("risk") or {}
    return "\n".join([
        f"### {item['short']} {item['subject']}",
        "",
        f"Risk: {risk.get('level', '?')} ({risk.get('why', '')})",
        f"Lane: {lane.id} ({lane.name}) on branch {lane.branch}",
        gate_line,
        f"Effect of including on master: {effects}",
        f"Files: {', '.join(item['files'])}",
        "",
        "Commit message:",
        "```",
        patch.get("message", ""),
        "```",
        "",
        "Diff:",
        "```diff",
        patch.get("patch", patch.get("error", "")),
        "```",
        "(diff cut off)" if patch.get("clipped") else "",
    ])


def write_packet(lane: C.Lane) -> Path:
    reports = gate_reports(lane)
    items = status.review_state(lane.worktree, lane=lane)["items"]
    counts = {level: sum(1 for i in items if (i.get("risk") or {}).get("level") == level) for level in ("high", "normal", "tests")}
    out = C.STATE / "review" / f"{lane.id}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    focus = lane.focus.read_text() if lane.focus.is_file() else ""
    parts = [
        f"# Review packet: {lane.name}",
        "",
        f"{len(items)} commits on {lane.branch} that master lacks, oldest first "
        f"({counts['high']} high risk, {counts['normal']} normal, {counts['tests']} tests only). "
        "Commits must be included in this order, so a dropped commit can strand later ones that build on it.",
        "",
        "## Lane focus given to the agent",
        focus,
        "## Reviewer instructions",
        (C.HOME / "smart_review.md").read_text(),
        "## Commits",
    ]
    parts += [commit_brief(lane, item, reports) for item in items]
    out.write_text("\n\n".join(parts))
    return out


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)


def review_checkout(lane: C.Lane, sha: str, suffix: str = "") -> Path:
    path = Path(f"/srv/factory/scratch/review-{lane.id}{suffix}")
    if not (path / ".git").exists():
        _git(lane.worktree, "worktree", "add", "--detach", str(path), sha)
    _git(path, "checkout", "--force", "--detach", sha)
    _git(path, "clean", "-fdq")
    node_modules = path / "infra/tv-streamer/node_modules"
    source = lane.worktree / "infra/tv-streamer/node_modules"
    if not node_modules.exists() and source.is_dir():
        subprocess.run(["cp", "-al", str(source), str(node_modules)], check=False)
    policy = json.loads((C.HOME / "opencode-policy.json").read_text())
    policy["policies"].append({"action": "tool.use", "resource": "edit:*", "effect": "deny"})
    (path / "opencode.json").write_text(json.dumps(policy, indent=2))
    return path


def record(row: dict) -> None:
    C.VERDICTS.parent.mkdir(parents=True, exist_ok=True)
    with C.VERDICTS.open("a") as handle:
        handle.write(json.dumps(row) + "\n")


def parse_verdict(text: str) -> dict | None:
    matches = VERDICT.findall(text)
    if not matches:
        return None
    try:
        row = json.loads(matches[-1])
    except json.JSONDecodeError:
        return None
    if row.get("verdict") not in ("keep", "drop", "fix"):
        return None
    return {"verdict": row["verdict"], "reason": str(row.get("reason", ""))[:800]}


def review_item(lane: C.Lane, item: dict, model: str, reports: dict[str, dict], suffix: str = "") -> dict | None:
    """Ask the reviewer about one commit and record the verdict."""
    checkout = review_checkout(lane, item["sha"], suffix)
    prompt = (C.HOME / "smart_review.md").read_text() + "\n\n" + commit_brief(lane, item, reports)
    env = {**os.environ, "TMPDIR": "/srv/factory/operator/tmp"}
    try:
        result = subprocess.run(
            ["opencode", "run", "--standalone", "--auto", "--model", model,
             "--title", f"review-{lane.id}-{item['short']}", prompt],
            cwd=checkout, capture_output=True, text=True, timeout=1800, env=env, check=False,
        )
    except subprocess.TimeoutExpired:
        return None
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", result.stdout + result.stderr)
    verdict = parse_verdict(text)
    if verdict is None:
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = {"sha": item["sha"], "short": item["short"], "subject": item.get("subject", ""),
           "lane": lane.id, "model": model, "at": stamp, **verdict}
    record(row)
    if verdict["verdict"] in ("drop", "fix"):
        add_lesson(lane, row)
    return row


def add_lesson(lane: C.Lane, row: dict, keep: int = 8) -> None:
    """Remember why the reviewer objected, so the lane's next passes see it."""
    path = lane.state / "lessons.jsonl"
    lines = path.read_text().splitlines() if path.is_file() else []
    lines.append(json.dumps({k: row[k] for k in ("short", "subject", "verdict", "reason") if k in row}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines[-keep:]) + "\n")


def lessons_text(lane: C.Lane) -> str:
    path = lane.state / "lessons.jsonl"
    if not path.is_file():
        return ""
    rows = []
    for line in path.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not rows:
        return ""
    out = ["## What the reviewer rejected recently in this lane (do not repeat these)"]
    for row in rows:
        out.append(f"- {row.get('verdict')}: {row.get('subject', '')} — {row.get('reason', '')}")
    return "\n".join(out)


def item_for(lane: C.Lane, sha: str) -> dict:
    """The review item for a commit, even if the queue would hide it."""
    for item in status.review_state(lane.worktree, lane=lane)["items"]:
        if item["sha"] == sha:
            return item
    out = _git(lane.worktree, "show", "-s", "--format=%h%x1f%s", sha).stdout.strip().split("\x1f")
    files = status._files(lane.worktree, sha)
    return {"sha": sha, "short": out[0] if out else sha[:8], "subject": out[-1] if out else "",
            "files": files, "effects": status.effects(files), "fixes": []}


def run_reviews(lane: C.Lane, model: str, limit: int | None) -> int:
    reports = gate_reports(lane)
    judged = status.verdicts()
    items = [i for i in status.review_state(lane.worktree, lane=lane)["items"] if i["sha"] not in judged]
    if limit:
        items = items[:limit]
    done = 0
    for item in items:
        row = review_item(lane, item, model, reports)
        if row is None:
            print(f"{item['short']}: no verdict parsed", flush=True)
            continue
        print(f"{item['short']}: {row['verdict']} - {row['reason']}", flush=True)
        done += 1
    return done


EXIT = {"keep": 0, "fix": 10, "drop": 11}


def review_one(lane: C.Lane, sha: str, model: str) -> int:
    """Inline review used by loop.sh. Prints the verdict as JSON; exit code says what to do."""
    row = review_item(lane, item_for(lane, sha), model, gate_reports(lane), suffix="-inline")
    if row is None:
        print(json.dumps({"verdict": "none", "reason": "The reviewer gave no verdict."}))
        return 12
    print(json.dumps(row))
    return EXIT[row["verdict"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    pk = sub.add_parser("packet")
    pk.add_argument("--lane", default="all")
    rn = sub.add_parser("run")
    rn.add_argument("--lane", required=True)
    rn.add_argument("--model", default=DEFAULT_REVIEWER)
    rn.add_argument("--limit", type=int)
    rn.add_argument("--parallel", action="store_true", help="one reviewer process per lane at once")
    one = sub.add_parser("one")
    one.add_argument("--lane", required=True)
    one.add_argument("--sha", required=True)
    one.add_argument("--model", default=DEFAULT_REVIEWER)
    ls = sub.add_parser("lessons")
    ls.add_argument("--lane", required=True)
    args = parser.parse_args()
    if args.cmd in ("one", "lessons"):
        lane = C.lane(args.lane)
        if lane is None:
            print("unknown lane", file=sys.stderr)
            return 2
        if args.cmd == "lessons":
            print(lessons_text(lane))
            return 0
        return review_one(lane, args.sha, args.model)
    chosen = C.lanes() if args.lane == "all" else [l for l in C.lanes() if l.id == args.lane]
    if not chosen:
        print("unknown lane", file=sys.stderr)
        return 2
    if args.cmd == "packet":
        for lane in chosen:
            print(write_packet(lane))
        return 0
    if args.parallel and len(chosen) > 1:
        procs = []
        for lane in chosen:
            cmd = [sys.executable, __file__, "run", "--lane", lane.id, "--model", args.model]
            if args.limit:
                cmd += ["--limit", str(args.limit)]
            log = (lane.state / "review.log").open("a")
            procs.append(subprocess.Popen(cmd, cwd=C.HOME, stdout=log, stderr=subprocess.STDOUT))
        codes = [proc.wait() for proc in procs]
        print(f"{len(procs)} lane reviews finished; see state/*/review.log")
        return 0 if not any(codes) else 1
    total = sum(run_reviews(lane, args.model, args.limit) for lane in chosen)
    print(f"{total} verdicts recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
