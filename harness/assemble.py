"""Build integrate/factory: every commit the reviewer kept or marked fix, onto master, in its own worktree.

Leaves /srv/factory/target and master untouched. Report: state/assemble.json.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import config as C
import picking

CHECKOUT = Path("/srv/factory/lanes/integrate")
BRANCH = "integrate/factory"
# Factory tooling, not product code (the board route is already live).
EXCLUDE = {"6ac7b25d"}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False).stdout.strip()


def main() -> int:
    data = json.loads((C.HOME / "research" / "data.json").read_text())
    rows = []
    for lane in data["lanes"]:
        for c in lane["commits"]:
            if c["verdict"] in ("keep", "fix") and c["sha"] not in EXCLUDE:
                rows.append({**c, "lane": lane["lane"], "full": git(C.WORKTREE, "rev-parse", c["sha"])})
    rows.sort(key=lambda r: r["at"])
    if not (CHECKOUT / ".git").exists():
        subprocess.run(["git", "-C", str(C.WORKTREE), "worktree", "add", "-q", "-b", BRANCH, str(CHECKOUT), C.MASTER], check=True)
    elif git(CHECKOUT, "rev-list", "--count", f"{C.MASTER}..HEAD") != "0":
        print(f"{BRANCH} already has commits; refusing to rebuild", file=sys.stderr)
        return 1
    applied, conflicts = [], []
    for row in rows:
        picked = picking.cherry_pick(CHECKOUT, row["full"])
        (applied if picked["ok"] else conflicts).append(
            {k: row[k] for k in ("lane", "sha", "verdict", "risk", "subject")} | ({} if picked["ok"] else {"files": picked["files"]})
        )
    report = {"base": git(CHECKOUT, "rev-parse", "--short", C.MASTER), "head": git(CHECKOUT, "rev-parse", "--short", "HEAD"),
              "candidates": len(rows), "applied": applied, "conflicts": conflicts}
    (C.STATE / "assemble.json").write_text(json.dumps(report, indent=1))
    print(f"applied {len(applied)}/{len(rows)} onto {report['base']}; head {report['head']}")
    for c in conflicts:
        print("  conflict", c["lane"], c["sha"], c["subject"][:70], c["files"][:3])
    return 0


if __name__ == "__main__":
    sys.exit(main())
