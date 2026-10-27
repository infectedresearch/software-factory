"""Cherry-pick with one safe automatic resolution.

Many factory commits each delete one entry from the BASELINE set in
tests/test_no_new_source_text_tests.py. Two deletions of neighbouring lines
conflict in git even though they are independent. When that file is the only
conflict and the commit only deletes entries from it, apply those deletions
to the current file and continue. Anything else aborts and reports the
conflicting files, leaving the checkout as it was.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

BASELINE_FILE = "tests/test_no_new_source_text_tests.py"
ENTRY = re.compile(r'^\s*"[^"]+",\s*$')
UNMERGED = ("UU", "AA", "DU", "UD", "AU", "UA", "DD")


def _git(repo: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        env=env,
    )


def baseline_deletions(repo: Path, sha: str) -> list[str] | None:
    """Entries the commit removes from the BASELINE file, or None if it does anything else there."""
    diff = _git(repo, "show", "--format=", "--unified=0", sha, "--", BASELINE_FILE).stdout
    removed = []
    for line in diff.splitlines():
        if line.startswith(("---", "+++", "@@", "diff ", "index ")):
            continue
        if line.startswith("+"):
            return None
        if line.startswith("-"):
            body = line[1:]
            if not ENTRY.match(body):
                return None
            removed.append(body.strip())
    return removed


def unmerged(repo: Path) -> list[str]:
    return [line[3:] for line in _git(repo, "status", "--porcelain").stdout.splitlines() if line[:2] in UNMERGED]


def cherry_pick(repo: Path, sha: str) -> dict:
    """Pick one commit. Returns {"ok": bool, "resolved": bool, "files": [...], "error": str}."""
    picked = _git(repo, "cherry-pick", "-x", sha)
    if picked.returncode == 0:
        return {"ok": True, "resolved": False, "files": [], "error": ""}
    conflicted = unmerged(repo)
    message = (picked.stderr or picked.stdout).strip().splitlines()
    if conflicted == [BASELINE_FILE]:
        removed = baseline_deletions(repo, sha)
        if removed is not None:
            ours = _git(repo, "show", f"HEAD:{BASELINE_FILE}").stdout
            drop = set(removed)
            kept = [line for line in ours.splitlines(keepends=True) if line.strip() not in drop]
            (repo / BASELINE_FILE).write_text("".join(kept))
            _git(repo, "add", BASELINE_FILE)
            env = {**os.environ, "GIT_EDITOR": "true"}
            cont = _git(repo, "cherry-pick", "--continue", env=env)
            if cont.returncode == 0:
                return {"ok": True, "resolved": True, "files": [], "error": ""}
            message = (cont.stderr or cont.stdout).strip().splitlines() or message
            conflicted = unmerged(repo) or conflicted
    _git(repo, "cherry-pick", "--abort")
    reason = next((l for l in message if "would be overwritten" in l or "conflict" in l.lower()), message[-1] if message else "")
    return {"ok": False, "resolved": False, "files": conflicted or ["(did not apply)"], "error": reason}
