"""Collect round 2 into research/round2/commits.json and usage.json.

Commits: every commit on r2/<lane> since the round's base. Commits the gate
rejected or the reviewer dropped were moved off the branch, so these are
exactly the commits that survived both. Each carries its inline review
verdict and, for card lanes, the card it built.

Usage: tokens per arm, per billing route (subscription vs API) and per role
(writing, scouting, review), from OpenCode's session table.
"""
from __future__ import annotations

import collections
import json
import re
import sqlite3
import subprocess
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]
OUT = Path(__file__).parent
REPO = Path("/srv/factory/target")
DB = "/srv/factory/operator/.local/share/opencode/opencode.db"
SINCE = "2026-11-09T22:00:00"
PRICES = {  # USD per million tokens, list price (round 1's costs.json sources)
    "grok": {"input": 2.0, "cached": 0.5, "output": 6.0},
    "opus": {"input": 4.0, "cached": 0.2, "cache_write": 5.0, "output": 20.0},
}


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True).stdout


def verdicts(lane: str) -> dict:
    out = {}
    path = HOME / "state" / lane / "verdicts.jsonl"
    for line in path.read_text().splitlines() if path.is_file() else []:
        row = json.loads(line)
        out[row["short"]] = row  # last verdict wins
    return out


def commits(design: dict) -> list[dict]:
    cards = {c["sha"]: c for c in json.loads((HOME / "state" / "cards" / "cards.json").read_text()) if c.get("sha")}
    rows = []
    for arm, spec in design["arms"].items():
        for lane in spec["lanes"]:
            judged = verdicts(lane)
            log = git("log", "--reverse", "--format=%H%x1f%h%x1f%cI%x1f%s%x1f%b%x1e", f"{design['base']}..r2/{lane}")
            for rec in filter(None, (r.strip() for r in log.split("\x1e"))):
                sha, short, at, subject, body = rec.split("\x1f")
                stat = git("show", "--numstat", "--format=", sha)
                added = removed = 0
                files = []
                for line in stat.splitlines():
                    a, r, f = line.split("\t", 2)
                    added += int(a) if a.isdigit() else 0
                    removed += int(r) if r.isdigit() else 0
                    files.append(f)
                v = judged.get(short) or judged.get(sha[:8]) or {}
                card = cards.get(short) or cards.get(sha[:8])
                fixes = re.search(r"^Fixes:\s*([0-9a-f]{7,40})", body, re.M)
                rows.append({
                    "sha": sha, "short": short, "lane": lane, "arm": arm, "at": at,
                    "subject": subject, "body": body.strip()[:2000],
                    "proof": (re.search(r"^Proof:\s*(.+)$", body, re.M) or [None, None])[1],
                    "fixes": fixes.group(1) if fixes else None,
                    "added": added, "removed": removed, "files": files,
                    "verdict": v.get("verdict"), "reason": v.get("reason"),
                    "card": card["id"] if card else None,
                    "card_class": card["class"] if card else None,
                })
    return rows


def usage() -> dict:
    db = sqlite3.connect(DB)
    rows = db.execute(
        "select title, model, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write "
        "from session_v2 where time_created > strftime('%s', ?)*1000", (SINCE,)).fetchall()
    design = json.loads((OUT / "design.json").read_text())
    card_areas = set(design["arms"]["cards"]["areas"])
    lane_arm = {l: a for a, s in design["arms"].items() for l in s["lanes"]}
    out: dict = collections.defaultdict(lambda: collections.Counter())
    for title, model, fresh, output, reasoning, cached, cache_write in rows:
        m = json.loads(model or "{}").get("id", "")
        route = "api" if m.endswith("-api") else "subscription"
        lane = re.match(r"^(free-\d|cards-\d)-\d+$", title or "")
        review = re.match(r"^review-(free-\d|cards-\d)-", title or "")
        scout = re.match(r"^scout-(lane-\d+)$", title or "")
        if lane:
            key = f"{lane_arm[lane.group(1)]}/writing/{route}"
        elif review:
            key = f"{lane_arm[review.group(1)]}/review"
        elif scout:
            key = f"{'cards' if scout.group(1) in card_areas else 'unused'}/scout/{route}"
        else:
            continue
        c = out[key]
        c["sessions"] += 1
        c["input"] += fresh or 0
        c["output"] += (output or 0) + (reasoning or 0)
        c["cached"] += cached or 0
        c["cache_write"] += cache_write or 0
    priced = {}
    for key, c in out.items():
        p = PRICES["opus" if "/review" in key else "grok"]
        usd = (c["input"] * p["input"] + c["cached"] * p["cached"] + c["output"] * p["output"]
               + c["cache_write"] * p.get("cache_write", 0)) / 1e6
        priced[key] = {**c, "usd": round(usd, 2)}
    return dict(sorted(priced.items()))


def main() -> None:
    design = json.loads((OUT / "design.json").read_text())
    rows = commits(design)
    (OUT / "commits.json").write_text(json.dumps(rows, indent=1))
    (OUT / "usage.json").write_text(json.dumps(usage(), indent=1))
    by = collections.Counter((r["arm"], r["verdict"]) for r in rows)
    print(len(rows), "commits;", dict(by))


if __name__ == "__main__":
    main()
