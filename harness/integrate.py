#!/usr/bin/env python3
"""Can tonight's output go onto master together?

    integrate.py            one check, writes state/integration.json
    integrate.py --every N  repeat every N seconds (the systemd unit does this)

Starting from current master in a scratch checkout, apply every lane's
waiting commits (not skipped, not dropped by the reviewer) in commit-time
order, then run the full gate on the result. Commits that do not apply are
listed with the files that collided; they are left out and the rest
continue.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config as C
import fullsuite
import picking
import status

BASE_CHECKOUT = Path("/srv/factory/scratch/fullsuite")

CHECKOUT = Path("/srv/factory/scratch/integration")
OUT = C.STATE / "integration.json"


def git(repo: Path, *args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, check=False)


def candidates() -> list[dict]:
    judged = status.verdicts()
    rows = []
    for lane in C.lanes():
        for item in status.review_state(lane.worktree, lane=lane)["items"]:
            # Every waiting commit counts. Dropping is the operator's
            # end-of-run review decision, and dropping one can strand the
            # commits that build on it.
            verdict = (judged.get(item["sha"]) or {}).get("verdict")
            when = git(lane.worktree, "show", "-s", "--format=%ct", item["sha"]).stdout.strip()
            rows.append({
                "lane": lane.id,
                "sha": item["sha"],
                "short": item["short"],
                "subject": item["subject"],
                "verdict": verdict,
                "when": int(when or 0),
            })
    rows.sort(key=lambda row: row["when"])
    return rows


def prepare(repo: Path) -> None:
    if not (CHECKOUT / ".git").exists():
        git(repo, "worktree", "add", "--detach", str(CHECKOUT), C.MASTER)
    git(CHECKOUT, "cherry-pick", "--abort")
    git(CHECKOUT, "checkout", "--force", "--detach", C.MASTER)
    git(CHECKOUT, "clean", "-fdq")
    modules = CHECKOUT / "infra/tv-streamer/node_modules"
    source = C.WORKTREE / "infra/tv-streamer/node_modules"
    if not modules.exists() and source.is_dir():
        subprocess.run(["cp", "-al", str(source), str(modules)], check=False)


TEST_REASONS = ("New node test failures", "New pytest failures", "Python does not compile", "JavaScript does not parse", "The gate did not finish")


def split_reasons(gate: dict) -> dict:
    """Separate broken tests from policy flags (forbidden files, BASELINE growth)."""
    reasons = gate.get("reasons") or []
    gate["test_failures"] = [r for r in reasons if r.startswith(TEST_REASONS)]
    gate["policy_flags"] = [r for r in reasons if not r.startswith(TEST_REASONS)]
    gate["tests_passed"] = not gate["test_failures"]
    return gate


def master_failures(master: str) -> list[str]:
    """Full-suite failures on master itself, cached until master moves."""
    cache = C.STATE / f"suite-master-{master}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text())["failed"]
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    if not (BASE_CHECKOUT / ".git").exists():
        git(C.WORKTREE, "worktree", "add", "--detach", str(BASE_CHECKOUT), C.MASTER)
    git(BASE_CHECKOUT, "checkout", "--force", "--detach", C.MASTER)
    git(BASE_CHECKOUT, "clean", "-fdq")
    failed, _ = fullsuite.run(BASE_CHECKOUT)
    for old in C.STATE.glob("suite-master-*.json"):
        old.unlink(missing_ok=True)
    cache.write_text(json.dumps({"failed": failed}))
    return failed


def full_suite_delta(master: str) -> dict:
    """Every Python test on the combined result, compared with master."""
    base = set(master_failures(master))
    failed, files = fullsuite.run(CHECKOUT)
    fresh = [t for t in failed if t not in base]
    if fresh:
        # Re-run the new failures once so a flaky test does not block the night.
        concrete = [t for t in fresh if not t.startswith("<")]
        _, again, _ = fullsuite._run(CHECKOUT, concrete, 1200) if concrete else (0, [], "")
        fresh = [t for t in fresh if t.startswith("<") or t in again]
    return {"files": files, "new": fresh, "preexisting": len(base)}


PICKED_FROM = re.compile(r"\(cherry picked from commit ([0-9a-f]{40})\)")
MAX_BISECTS = 3


def test_command(test: str) -> str:
    """Shell command that exits 0 if the test passes, 1 if it fails, 125 if it does not exist yet."""
    path = test.split("::", 1)[0]
    if test.endswith((".test.js", ".test.cjs", ".test.mjs")):
        run = f"node --test {shlex.quote(test)}"
    else:
        run = f"uv run -q pytest -q -p no:cacheprovider {shlex.quote(test)}"
    return f"[ -f {shlex.quote(path)} ] || exit 125; {run} >/dev/null 2>&1 || exit 1"


def bisect(test: str) -> str | None:
    """Original sha of the first combined commit that breaks ``test``, or None."""
    head = git(CHECKOUT, "rev-parse", "HEAD").stdout.strip()
    git(CHECKOUT, "bisect", "reset")
    git(CHECKOUT, "bisect", "start", head, C.MASTER)
    result = git(CHECKOUT, "bisect", "run", "sh", "-c", test_command(test), timeout=1800)
    # Read Git's result ref rather than parsing version-dependent console text.
    bad = git(CHECKOUT, "rev-parse", "--verify", "refs/bisect/bad").stdout.strip() if result.returncode == 0 else ""
    git(CHECKOUT, "bisect", "reset")
    git(CHECKOUT, "checkout", "--force", "--detach", head)
    if not re.fullmatch(r"[0-9a-f]{40}", bad):
        print(f"bisect failed for {test}: {result.stdout}\n{result.stderr}", file=sys.stderr)
        return None
    message = git(CHECKOUT, "show", "-s", "--format=%B", bad).stdout
    origin = PICKED_FROM.search(message)
    return origin.group(1) if origin else None


def attribute(new_failures: list[str], applied: list[dict]) -> list[dict]:
    """Which lane's commit broke each new failure, so that lane can fix it."""
    by_sha = {row["sha"]: row for row in applied}
    blamed = []
    for test in new_failures[:MAX_BISECTS]:
        if test.startswith("<"):
            continue
        sha = bisect(test)
        row = by_sha.get(sha or "")
        if row:
            blamed.append({"test": test, **{k: row[k] for k in ("lane", "sha", "short", "subject")}})
    return blamed


def write_feedback(blamed: list[dict]) -> None:
    """Tell each lane about combined-result breakage it caused; clear the rest."""
    by_lane: dict[str, list[dict]] = {}
    for row in blamed:
        by_lane.setdefault(row["lane"], []).append(row)
    for lane in C.lanes():
        path = lane.state / "feedback.txt"
        rows = by_lane.get(lane.id)
        if not rows:
            path.unlink(missing_ok=True)
            continue
        lines = ["## Must fix first: your commit breaks a test when combined with master and the other lanes"]
        for row in rows:
            lines.append(f"- {row['short']} ({row['subject']}) breaks {row['test']}.")
        lines.append("Fix this before anything else, in one small commit whose body has the line: Fixes: <that short sha>.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n")


def check() -> dict:
    started = time.time()
    rows = candidates()
    prepare(C.WORKTREE)
    master = git(CHECKOUT, "rev-parse", "--short", "HEAD").stdout.strip()
    applied, conflicts, resolved = [], [], 0
    for row in rows:
        picked = picking.cherry_pick(CHECKOUT, row["sha"])
        if picked["ok"]:
            applied.append(row)
            resolved += int(picked["resolved"])
            continue
        conflicts.append({**row, "files": picked["files"]})
    report: dict = {
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "master": master,
        "candidates": len(rows),
        "applied": len(applied),
        "baseline_merges": resolved,
        "kept_by_reviewer": sum(1 for r in applied if r["verdict"] == "keep"),
        "unreviewed": sum(1 for r in applied if not r["verdict"]),
        "conflicts": conflicts,
        "per_lane": {},
    }
    for row in applied:
        report["per_lane"][row["lane"]] = report["per_lane"].get(row["lane"], 0) + 1
    if applied:
        env = {**os.environ, "FACTORY_SCRATCH": "/srv/factory/scratch/integration-base"}
        gate_out = C.STATE / "integration-gate.json"
        subprocess.run(
            [sys.executable, str(C.HOME / "gate.py"), "--repo", str(CHECKOUT), "--base", C.MASTER,
             "--out", str(gate_out), "--audit", "--no-pytest"],
            cwd=C.HOME, env=env, capture_output=True, text=True, timeout=3600, check=False,
        )
        try:
            gate = json.loads(gate_out.read_text())
        except (OSError, json.JSONDecodeError):
            gate = {"passed": False, "reasons": ["The gate did not finish."]}
        report["gate"] = {k: gate.get(k) for k in ("passed", "reasons", "preexisting", "seconds", "node_files")}
        suite = full_suite_delta(master)
        report["gate"]["pytest_files"] = suite["files"]
        report["gate"]["pytest_new"] = suite["new"]
        report["gate"]["pytest_preexisting"] = suite["preexisting"]
        if suite["new"]:
            report["gate"]["reasons"] = (report["gate"].get("reasons") or []) + [
                "New pytest failures: " + ", ".join(suite["new"][:15])
            ]
        split_reasons(report["gate"])
        node_new = []
        for reason in report["gate"]["test_failures"]:
            if reason.startswith("New node test failures: "):
                node_new += [f.strip() for f in reason.split(": ", 1)[1].split(",")]
        report["blamed"] = attribute(suite["new"] + node_new, applied)
    else:
        report["blamed"] = []
    write_feedback(report["blamed"])
    report["seconds"] = round(time.time() - started, 1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--every", type=int, default=0)
    args = parser.parse_args()
    while True:
        report = check()
        gate = report.get("gate") or {}
        print(f"{report['at']} applied {report['applied']}/{report['candidates']}, "
              f"{len(report['conflicts'])} conflicts, tests {'passed' if gate.get('passed') else 'FAILED' if gate else 'not run'}",
              flush=True)
        if not args.every:
            return 0
        # One last check after the lanes' end time, then stop.
        if datetime.now(timezone.utc) >= datetime(2026, 10, 27, 9, 0, tzinfo=timezone.utc):
            return 0
        time.sleep(args.every)


if __name__ == "__main__":
    sys.exit(main())
