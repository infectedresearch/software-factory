"""Operator-approved task cards for card lanes.

A scout pass reads one area of the repository and proposes cards. Nothing is
built from a proposal until the operator accepts it on the board (or here).
Card lanes then take accepted cards one at a time, oldest first, from the
areas assigned to them. One card is one small verified commit.

    python3 cards.py scout --areas lane-1,lane-2      # propose (read-only pass)
    python3 cards.py list [--state proposed]
    python3 cards.py accept 12 13 / decline 14 --note "..."
    python3 cards.py next --lane cards-1               # used by loop.sh
    python3 cards.py done --lane cards-1 --sha abc123  # used by loop.sh
    python3 cards.py strike --lane cards-1 --why "..." # used by loop.sh
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import config as C

# One queue shared by every lane. Lanes run with a per-lane FACTORY_STATE, so
# this must not follow C.STATE.
CARDS_DIR = Path(os.environ.get("FACTORY_CARDS_DIR", C.HOME / "state" / "cards"))
STORE = CARDS_DIR / "cards.json"
LOCK = CARDS_DIR / "lock"
SCOUT_DIR = CARDS_DIR / "scout"
SCOUT_WORKTREE = Path("/srv/factory/lanes/scout")
CLASSES = ("security-or-safety", "user-facing-bug", "reliability", "meaningful-test")
MAX_STRIKES = 2

SCOUT_PROMPT = """You are a scout for an unattended software factory. You READ code and propose work; you must not edit, commit, or run anything that changes files. Builders will later implement each card as one small verified commit (at most 300 production lines), so every card must be that size.

Area you are scouting:
{focus}

Propose {n} cards for this area. Spend your effort verifying each claim by reading the actual code; cite file paths and function names you have read. A card that a reviewer cannot confirm from the code is worthless and will be declined.

Each card must be one of these classes, nothing else:
- security-or-safety: a permission, validation, injection, secret-handling or abuse-limit gap in a real request or message path.
- user-facing-bug: behaviour a real user can trigger that is wrong, with the input that triggers it.
- reliability: a crash, hang, unbounded growth, silent error swallow, or missing retry/timeout in a long-running path.
- meaningful-test: a real decision in production code that has no behaviour test, where the test would call the real function/handler with inputs and assert on what it does.

Do NOT propose: refactors, renames, formatting, comments, docs, new abstractions, tests that read our own source text, changes to page styling or copy, anything in agent.py / personas / prompts / tool routing, viewer playback (app.js, abr-core.js, live-hls.js, player-states.js, mediamtx-worker.js), deployment scripts, or config files. Do not propose "minor" work: if a senior engineer would not bother, leave it out. Fewer good cards beat {n} weak ones.

Output ONLY a JSON array, no prose before or after, where each element is:
{{"title": "<=80 chars, imperative", "class": "<one of the four>", "files": ["path", ...], "brief": "2-4 sentences: what is wrong or untested, where (file:function), and why it matters. Include the triggering input if it is a bug.", "proof": "the exact test to add or command to run that fails on the current code and passes once the card is done"}}"""


# ---------- store ----------

def _load() -> list[dict]:
    try:
        return json.loads(STORE.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _save(cards: list[dict]) -> None:
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cards, indent=1, ensure_ascii=False))
    os.replace(tmp, STORE)


class locked:
    """Lanes call next/done/strike concurrently; serialise every read-modify-write."""

    def __enter__(self):
        CARDS_DIR.mkdir(parents=True, exist_ok=True)
        self.fh = open(LOCK, "w")
        fcntl.flock(self.fh, fcntl.LOCK_EX)
        self.cards = _load()
        return self.cards

    def __exit__(self, *exc):
        if exc[0] is None:
            _save(self.cards)
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9_]+", text.lower()) if len(w) > 2}


def duplicate_of(card: dict, cards: list[dict]) -> int | None:
    title, brief = _words(card["title"]), _words(card["brief"])
    for other in cards:
        if other["state"] == "declined":
            continue
        t, b = _words(other["title"]), _words(other["brief"])
        title_sim = len(title & t) / (len(title | t) or 1)
        brief_sim = len(brief & b) / (len(brief | b) or 1)
        if title_sim > 0.7 or brief_sim > 0.55:
            return other["id"]
    return None


def add(card: dict, cards: list[dict], source: str, area: str) -> dict | None:
    if duplicate_of(card, cards) is not None:
        return None
    cards.append({
        "id": max((c["id"] for c in cards), default=0) + 1,
        "title": card["title"][:120],
        "class": card["class"],
        "files": [str(f)[:200] for f in card.get("files", [])][:12],
        "brief": card["brief"][:1500],
        "proof": card["proof"][:800],
        "area": area,
        "source": source,
        "state": "proposed",
        "note": "",
        "lane": None,
        "strikes": 0,
        "sha": None,
        "created_at": time.time(),
        "decided_at": None,
        "assigned_at": None,
        "finished_at": None,
    })
    return cards[-1]


# ---------- scout ----------

def area_focus(area: str) -> str:
    path = C.HOME / "focus" / f"{area}.md"
    text = path.read_text()
    # The first line names the round-1 lane; the scout only needs the area.
    return "\n".join(l for l in text.splitlines() if not l.startswith("## Your lane"))


def parse_cards(text: str) -> list[dict]:
    """Find the JSON array in a model answer; tolerate fences and prose."""
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    candidates = []
    for m in re.finditer(r"\[", text):
        depth = 0
        for i in range(m.start(), len(text)):
            if text[i] == "[":
                depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    candidates.append(text[m.start():i + 1])
                    break
    out = []
    for chunk in sorted(candidates, key=len, reverse=True):
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
            for x in data:
                if {"title", "class", "brief", "proof"} <= set(x) and x["class"] in CLASSES:
                    out.append(x)
            if out:
                return out
    return out


def ensure_scout_worktree() -> None:
    repo = Path(os.environ.get("FACTORY_REPO", "/srv/factory/target"))
    if not (SCOUT_WORKTREE / ".git").exists():
        subprocess.run(["git", "-C", str(repo), "worktree", "add", "--detach", str(SCOUT_WORKTREE), C.MASTER], check=True)
    subprocess.run(["git", "-C", str(SCOUT_WORKTREE), "checkout", "--force", "--detach", C.MASTER], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(SCOUT_WORKTREE), "clean", "-fdq"], check=True)
    (SCOUT_WORKTREE / "opencode.json").write_text((C.HOME / "opencode-policy.json").read_text())


def scout_area(area: str, n: int, model: str, timeout: int) -> tuple[str, list[dict], str]:
    SCOUT_DIR.mkdir(parents=True, exist_ok=True)
    out = SCOUT_DIR / f"{area}-{int(time.time())}.log"
    prompt = SCOUT_PROMPT.format(focus=area_focus(area), n=n)
    with out.open("w") as fh:
        proc = subprocess.run(
            ["opencode", "run", "--standalone", "--auto", "--model", model, "--title", f"scout-{area}", prompt],
            cwd=SCOUT_WORKTREE, stdout=fh, stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False,
            # opencode takes its project directory from PWD, which subprocess's
            # cwd= does not update.
            env={**os.environ, "TMPDIR": "/srv/factory/operator/tmp", "PWD": str(SCOUT_WORKTREE)},
        )
    cards = parse_cards(out.read_text())
    return area, cards, f"exit={proc.returncode} log={out.name}"


def store_proposals(area: str, proposals: list[dict], source: str) -> tuple[int, int]:
    added = dupes = 0
    with locked() as cards:
        for p in proposals:
            if add(p, cards, source=source, area=area):
                added += 1
            else:
                dupes += 1
    return added, dupes


def scout(areas: list[str], per_area: int, model: str, parallel: int, timeout: int) -> dict:
    ensure_scout_worktree()
    CARDS_DIR.mkdir(parents=True, exist_ok=True)
    (CARDS_DIR / "scout.pid").write_text(str(os.getpid()))
    summary = {"areas": {}, "added": 0, "duplicates": 0}
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(scout_area, a, per_area, model, timeout) for a in areas]
            for fut in as_completed(futures):
                try:
                    area, proposals, info = fut.result()
                except Exception as exc:  # one area failing must not lose the others
                    print(f"scout area failed: {exc}", file=sys.stderr)
                    continue
                # Store as each area finishes so the board fills up incrementally.
                added, dupes = store_proposals(area, proposals, f"scout:{model}")
                summary["areas"][area] = {"proposed": len(proposals), "added": added, "info": info}
                summary["added"] += added
                summary["duplicates"] += dupes
                print(json.dumps({"area": area, "proposed": len(proposals), "added": added}), flush=True)
    finally:
        (CARDS_DIR / "scout.pid").unlink(missing_ok=True)
        subprocess.run(["git", "-C", str(SCOUT_WORKTREE), "checkout", "--", "."], check=False, capture_output=True)
        subprocess.run(["git", "-C", str(SCOUT_WORKTREE), "clean", "-fdq"], check=False)
    return summary


def ingest(log: Path, area: str, source: str = "scout:log") -> tuple[int, int]:
    """Store the proposals from a finished scout log (recovery path)."""
    return store_proposals(area, parse_cards(log.read_text()), source)


# ---------- lane queue ----------

def render_focus(card: dict) -> str:
    files = "\n".join(f"- {f}" for f in card["files"]) or "- (see brief)"
    note = f"\nNotes from the approver:\n{card['note']}\n" if card["note"] else ""
    return f"""## Your lane: card {card['id']}: {card['title']}

Build exactly what this card asks for and nothing else. Class: {card['class']}.

{card['brief']}

Files the scout read:
{files}

Proof the card is done (your commit must include this, and its Proof: line must name it):
{card['proof']}
{note}
Verify the claim first: if the code does not actually have this problem, make no commit and say so in one paragraph. One small verified commit, then stop.
"""


def next_card(lane: str, areas: list[str], focus_path: Path) -> dict | None:
    with locked() as cards:
        # Finish a card already assigned to this lane before taking another
        # (the loop restarted mid-card).
        mine = [c for c in cards if c["state"] == "assigned" and c["lane"] == lane]
        if mine:
            card = mine[0]
        else:
            pool = [c for c in cards if c["state"] == "accepted" and (not areas or c["area"] in areas)]
            if not pool:
                return None
            card = min(pool, key=lambda c: (c["decided_at"] or 0, c["id"]))
            card.update(state="assigned", lane=lane, assigned_at=time.time())
        focus_path.write_text(render_focus(card))
        return card


def finish(lane: str, state: str, sha: str | None = None, why: str = "") -> dict | None:
    with locked() as cards:
        mine = [c for c in cards if c["state"] == "assigned" and c["lane"] == lane]
        if not mine:
            return None
        card = mine[0]
        if state == "strike":
            card["strikes"] += 1
            card["note"] = (card["note"] + f"\nstrike {card['strikes']}: {why[:300]}").strip()
            if card["strikes"] < MAX_STRIKES:
                return card
            state = "failed"
        card.update(state=state, sha=sha, finished_at=time.time())
        return card


def decide(ids: list[int], state: str, note: str = "") -> list[dict]:
    out = []
    with locked() as cards:
        for c in cards:
            if c["id"] in ids and c["state"] in ("proposed", "accepted", "declined"):
                c.update(state=state, decided_at=time.time())
                if note:
                    c["note"] = note
                out.append(c)
    return out


def counts(cards: list[dict] | None = None) -> dict:
    cards = _load() if cards is None else cards
    out: dict = {}
    for c in cards:
        out[c["state"]] = out.get(c["state"], 0) + 1
    return out


# ---------- cli ----------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scout")
    s.add_argument("--areas", default=",".join(f"lane-{i}" for i in range(1, 15)))
    s.add_argument("--per-area", type=int, default=6)
    s.add_argument("--model", default=os.environ.get("FACTORY_SCOUT_MODEL", C.DEFAULT_MODEL))
    s.add_argument("--parallel", type=int, default=7)
    s.add_argument("--timeout", type=int, default=1500)
    l = sub.add_parser("list")
    l.add_argument("--state")
    for name in ("accept", "decline"):
        d = sub.add_parser(name)
        d.add_argument("ids", type=int, nargs="+")
        d.add_argument("--note", default="")
    n = sub.add_parser("next")
    n.add_argument("--lane", required=True)
    n.add_argument("--areas", default=os.environ.get("FACTORY_AREAS", ""))
    n.add_argument("--focus", default=os.environ.get("FACTORY_FOCUS", ""))
    d = sub.add_parser("done")
    d.add_argument("--lane", required=True)
    d.add_argument("--sha", required=True)
    k = sub.add_parser("strike")
    k.add_argument("--lane", required=True)
    k.add_argument("--why", default="")
    sub.add_parser("stats")
    g = sub.add_parser("ingest")
    g.add_argument("log")
    g.add_argument("--area", required=True)
    a = p.parse_args()

    if a.cmd == "scout":
        areas = [x for x in a.areas.split(",") if x]
        print(json.dumps(scout(areas, a.per_area, a.model, a.parallel, a.timeout), indent=1))
    elif a.cmd == "list":
        for c in _load():
            if not a.state or c["state"] == a.state:
                print(f"#{c['id']:<4}{c['state']:<9}{c['class']:<19}{c['area']:<8}{c['title']}")
    elif a.cmd in ("accept", "decline"):
        print(f"{a.cmd}: {[c['id'] for c in decide(a.ids, 'accepted' if a.cmd == 'accept' else 'declined', a.note)]}")
    elif a.cmd == "next":
        card = next_card(a.lane, [x for x in a.areas.split(",") if x], Path(a.focus))
        print(card["id"] if card else "none")
    elif a.cmd == "done":
        card = finish(a.lane, "done", sha=a.sha)
        print(card["id"] if card else "none")
    elif a.cmd == "strike":
        card = finish(a.lane, "strike", why=a.why)
        print(f"{card['id']} {card['state']}" if card else "none")
    elif a.cmd == "ingest":
        print(ingest(Path(a.log), a.area))
    elif a.cmd == "stats":
        print(json.dumps(counts(), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
