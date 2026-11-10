"""Recompute round 2's headline numbers (FL-TR-2026-03) from research/round2/data.json."""
from __future__ import annotations

import json
import sys
from pathlib import Path

DATA = json.loads((Path(__file__).resolve().parent / "research/round2/data.json").read_text())


def check(name: str, got, want) -> bool:
    ok = got == want
    print(f"{'ok ' if ok else 'BAD'} {name}: {got}" + ("" if ok else f" (published {want})"))
    return ok


def main() -> int:
    commits = DATA["commits"]
    merged = [c for c in commits if c["merged"]]
    r2 = DATA["round_comparison"]["round2"]
    arms = DATA["arms"]
    results = [
        check("surviving commits", len(commits), arms["free"]["commits"] + arms["cards"]["commits"]),
        check("merged", len(merged), r2["integrated"]),
        check("real bugs merged", sum(c["real_bug"] for c in merged), r2["real"]),
        check("minor merged", sum(c["class"] == "minor" for c in merged), r2["minor"]),
        check("per real fix", round(r2["tokens_usd"] / r2["real"], 2), round(r2["per_real"], 2)),
    ]
    for arm, a in arms.items():
        nonminor = [c for c in merged if c["arm"] == arm and c["class"] != "minor"]
        results.append(check(f"{arm}: non-minor merged", len(nonminor), a["nonminor_merged"]))
        results.append(check(f"{arm}: primary (writing + scouting per non-minor merged)",
                             round((a["write"] + a["scout"]) / len(nonminor), 2), round(a["primary"], 2)))
    diff = (arms["cards"]["primary"] - arms["free"]["primary"]) / arms["free"]["primary"]
    results.append(check("primary difference", round(diff, 3), round(DATA["primary_diff"], 3)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
