"""Round 2: free-roaming lanes vs operator-approved cards, 7 lanes each.

    python3 round2.py setup [--seed N] [--hours H]   # lanes, worktrees, design record
    python3 round2.py check                          # is everything in place to start?
    python3 round2.py start                          # start all 14 lanes + integration
    python3 round2.py stop                           # graceful stop after current passes

The 14 round-1 areas are split at random (recorded seed) into the two arms,
so area difficulty is not confounded with the treatment. Free lanes get an
area's round-1 focus; card lanes take accepted cards from the card arm's
areas, oldest first. Same model, prompt, gate, reviewer and hours for both.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

import config as C

REPO = Path("/srv/factory/target")
LANES_ROOT = Path("/srv/factory/lanes")
ROUND1 = C.HOME / "lanes" / "round1"
DESIGN = C.HOME / "research" / "round2" / "design.json"
AREAS = [f"lane-{i}" for i in range(1, 15)]


def git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def split_areas(seed: int) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    areas = AREAS[:]
    rng.shuffle(areas)
    return sorted(areas[:7], key=lambda a: int(a.split("-")[1])), sorted(areas[7:], key=lambda a: int(a.split("-")[1]))


def free_focus(area: str) -> str:
    text = (C.HOME / "focus" / f"{area}.md").read_text().rstrip()
    return text + """

Round 2 note: this area was worked for one night already and the obvious bugs in it were fixed and shipped. Look past the surface: request handlers with no behaviour test, inputs nobody validates, state that grows without bound, errors swallowed silently. Prove each bug with a real input before fixing it.
"""


def env_text(lane: str, areas: list[str], hours: int) -> str:
    lines = [
        f"FACTORY_LANE={lane}",
        f"FACTORY_WORKTREE={LANES_ROOT / lane}",
        f"FACTORY_BRANCH=r2/{lane}",
        f"FACTORY_STATE={C.STATE / lane}",
        f"FACTORY_TITLE={lane}",
        f"FACTORY_SCRATCH=/srv/factory/scratch/scratch-{lane}",
        f"FACTORY_FOCUS={C.HOME / 'focus' / lane}.md",
        f"FACTORY_HOURS={hours}",
        f"FACTORY_REPO={REPO}",
    ]
    if lane.startswith("cards-"):
        lines.append("FACTORY_AREAS=" + ",".join(areas))
    return "\n".join(lines) + "\n"


def setup(seed: int, hours: int) -> dict:
    free_areas, card_areas = split_areas(seed)
    ROUND1.mkdir(exist_ok=True)
    for env in C.LANES_DIR.glob("*.env"):
        shutil.move(str(env), ROUND1 / env.name)
    for wt in sorted(LANES_ROOT.glob("lane-*")) + [LANES_ROOT / "card-16"]:
        if (wt / ".git").exists():
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], check=False, capture_output=True)
    (C.STATE / "round1").mkdir(exist_ok=True)
    for st in list(C.STATE.glob("lane-*")) + [C.STATE / "card-16"]:
        if st.is_dir():
            shutil.move(str(st), C.STATE / "round1" / st.name)
    (C.STATE / "empties.log").unlink(missing_ok=True)

    master = git("rev-parse", "master")
    lanes = []
    for i, area in enumerate(free_areas, 1):
        lane = f"free-{i}"
        (C.HOME / "focus" / f"{lane}.md").write_text(free_focus(area))
        lanes.append((lane, [area]))
    for i in range(1, 8):
        lane = f"cards-{i}"
        (C.HOME / "focus" / f"{lane}.md").write_text(f"## Your lane: {lane}\n\nWaiting for an accepted card.\n")
        lanes.append((lane, card_areas))
    for lane, areas in lanes:
        (C.LANES_DIR / f"{lane}.env").write_text(env_text(lane, areas, hours))
        wt = LANES_ROOT / lane
        if (wt / ".git").exists():
            subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)], check=False, capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "branch", "-D", f"r2/{lane}"], check=False, capture_output=True)
        git("worktree", "add", "-b", f"r2/{lane}", str(wt), master)
        (C.STATE / lane).mkdir(parents=True, exist_ok=True)

    (C.STATE / "inline_review").write_text("on\n")
    (C.STATE / "review_model").write_text(C.DEFAULT_REVIEWER + "\n")
    (C.STATE / "model").write_text(C.DEFAULT_MODEL + "\n")

    design = {
        "round": 2,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base": master,
        "seed": seed,
        "hours": hours,
        "model": C.DEFAULT_MODEL,
        "reviewer": C.DEFAULT_REVIEWER,
        "inline_review": True,
        "arms": {
            "free": {"lanes": [f"free-{i}" for i in range(1, 8)], "areas": free_areas},
            "cards": {"lanes": [f"cards-{i}" for i in range(1, 8)], "areas": card_areas},
        },
        "harness_changes_since_round_1": [
            "Proof: line required by the gate and run by the reviewer",
            "prompt: reproduce before fixing; value classes shown; turn budget; no git stash",
            "leftovers saved as patches and discarded instead of git stash",
            "lane sees its own branch log each pass",
            "synchronized-empty detector stops all lanes",
            "sanitized bots_config.json fixture in every checkout (80 -> ~21 pre-existing failures)",
            "pass timeout 2400s -> 1500s",
            "inline review on from the start",
        ],
    }
    DESIGN.parent.mkdir(parents=True, exist_ok=True)
    DESIGN.write_text(json.dumps(design, indent=1))
    return design


def check() -> list[str]:
    problems = []
    design = json.loads(DESIGN.read_text()) if DESIGN.is_file() else None
    if not design:
        return ["no design.json; run setup"]
    if git("rev-parse", "master") != design["base"]:
        problems.append("master moved since setup; lanes branch from the old base (re-run setup)")
    dirty = [l for l in git("status", "--porcelain").splitlines() if not l.startswith("??")]
    if dirty:
        problems.append(f"warning: {len(dirty)} modified tracked files in {REPO} (someone's in-progress work); lanes start fine, but including commits onto master will be refused until they are committed or stashed")
    for lane in C.lanes():
        if not (lane.worktree / ".git").exists():
            problems.append(f"{lane.id}: worktree missing")
        elif git("rev-parse", "--abbrev-ref", "HEAD", cwd=lane.worktree) != lane.branch:
            problems.append(f"{lane.id}: worktree not on {lane.branch}")
    import cards
    counts = cards.counts()
    if not counts.get("accepted"):
        problems.append("no accepted cards; card lanes would stop immediately")
    if (C.STATE / "inline_review").read_text().strip() != "on":
        problems.append("inline review is off")
    if (C.STATE / "cards" / "scout.pid").exists():
        problems.append("scout still running (fine, but cards will keep arriving)")
    return problems


def start() -> None:
    problems = [p for p in check() if "scout still running" not in p and not p.startswith("warning:")]
    if problems:
        print("not starting:\n  " + "\n  ".join(problems))
        sys.exit(1)
    (C.STATE / "factory_on").write_text("on\n")
    for lane in C.lanes():
        lane.stopfile.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "start", lane.unit], check=True)
        print("started", lane.unit)
    subprocess.run(["systemctl", "--user", "start", "grok-factory-integrate"], check=True)
    print("started grok-factory-integrate")


def stop() -> None:
    for lane in C.lanes():
        lane.stopfile.touch()
    print("stop files written; each lane stops after its current pass")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup")
    s.add_argument("--seed", type=int, default=20261109)
    s.add_argument("--hours", type=int, default=9)
    sub.add_parser("check")
    sub.add_parser("start")
    sub.add_parser("stop")
    a = p.parse_args()
    if a.cmd == "setup":
        d = setup(a.seed, a.hours)
        print(json.dumps(d["arms"], indent=1))
    elif a.cmd == "check":
        probs = check()
        print("ready" if not probs else "\n".join(probs))
    elif a.cmd == "start":
        start()
    elif a.cmd == "stop":
        stop()


if __name__ == "__main__":
    main()
