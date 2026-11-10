"""Per-arm tally for round 2 from the lane logs and OpenCode's session table.

Dollar figures are public list prices (grok-4.7: $2/M input, $0.50/M cached,
$6/M output+reasoning) so the arms can be compared; the subscription bills $0.
"""
import collections
import glob
import json
import re
import sqlite3
from pathlib import Path

HOME = Path(__file__).resolve().parents[2]
DB = "/srv/factory/operator/.local/share/opencode/opencode.db"
SINCE = "2026-11-09T22:00:00"
PATTERNS = [
    ("passes", r" pass \d+ begin"), ("no_commit", r"no-commit"), ("gate_pass", r"gate passed"),
    ("gate_fail", r"gate failed"), ("keep", r"review keep"), ("fix", r"review fix"),
    ("drop", r"review drop"), ("timeout", r" timeout$"),
]


def lane_counts() -> dict:
    arms = {"free": collections.Counter(), "cards": collections.Counter()}
    for f in sorted(glob.glob(str(HOME / "state" / "*-[0-9]" / "loop.log"))):
        lane = Path(f).parent.name
        if not lane.startswith(("free-", "cards-")):
            continue
        for line in open(f, errors="replace"):
            if not re.match(r"^\d{4}-\d\d-\d\dT", line):
                continue
            for key, pat in PATTERNS:
                if re.search(pat, line):
                    arms[lane.split("-")[0]][key] += 1
    return arms


def usage(db, where: str) -> dict:
    row = db.execute(
        "select count(*), sum(tokens_input), sum(tokens_output)+sum(tokens_reasoning), sum(tokens_cache_read) "
        f"from session_v2 where {where} and time_created > strftime('%s', ?)*1000", (SINCE,)
    ).fetchone()
    n, fresh, out, cached = [x or 0 for x in row]
    return {"sessions": n, "fresh": fresh, "out": out, "cached": cached,
            "usd": round((fresh * 2 + cached * 0.5 + out * 6) / 1e6, 2)}


def main() -> None:
    db = sqlite3.connect(DB)
    design = json.loads((HOME / "research" / "round2" / "design.json").read_text())
    arms = lane_counts()
    scout = sum(usage(db, f"title = 'scout-{a}'")["usd"] for a in design["arms"]["cards"]["areas"])
    out = {"scout_usd_card_areas": round(scout, 2)}
    for arm, c in arms.items():
        u = usage(db, f"title like '{arm}-%'")
        kept = c["keep"] + c["fix"]
        out[arm] = {**c, **u, "kept": kept,
                    "usd_per_kept": round(u["usd"] / kept, 2) if kept else None,
                    "cached_per_session_M": round(u["cached"] / max(u["sessions"], 1) / 1e6, 2)}
    k = out["cards"]["kept"]
    out["cards"]["usd_per_kept_incl_scout"] = round((out["cards"]["usd"] + scout) / k, 2) if k else None
    cards = json.loads((HOME / "state" / "cards" / "cards.json").read_text())
    out["card_states"] = dict(collections.Counter(c["state"] for c in cards))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
