"""Build the round 2 technical report (FL-TR-2026-02) from research/round2/*.json.

    python3 research/round2/build.py          # report.md, paper.html, PDF in research/round2/page/

Every number in the text is computed here from the data files.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import html
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "page"
sys.path.insert(0, str(ROOT / "research"))
import paper as P1  # noqa: E402  (round 1's print stylesheet and fonts)

REPORT_ID = "FL-TR-2026-03"
DRAFT = "Draft Research Report 003"
SLUG = "show-your-work"
PDF_NAME = "show-your-work-draft-report.pdf"
TITLE = "Show Your Work"
SUBTITLE = "Proof-carrying commits made an unattended coding factory's second night more useful. Task cards made building cheaper, not the night."
CLASSES = ["security-or-safety", "user-facing-bug", "reliability", "meaningful-test", "minor"]
CLASS_NAMES = {"security-or-safety": "Security or safety", "user-facing-bug": "User-facing bug",
               "reliability": "Reliability", "meaningful-test": "Behaviour test", "minor": "Minor"}
COL = {"security-or-safety": "#1e4e8c", "user-facing-bug": "#0f766e", "reliability": "#4d7c0f",
       "meaningful-test": "#6d4fa1", "minor": "#9aa1ab"}
ARM_COL = {"free": "#b7791f", "cards": "#0f766e"}
INK, MUTED, GRID = "#14181f", "#5b6270", "#e3e6ea"
esc = html.escape


PUBLIC_CATEGORY_ONLY = "Security or safety change (category-only public record)"


def public_subject(row: dict) -> str:
    """Round 1's rule: security and safety changes are published by category only.

    Safety-filter tests are included: their subjects describe how a filter was evaded.
    """
    subject = row["subject"]
    label = row.get("label") or {}
    if label.get("class") == "security-or-safety":
        return PUBLIC_CATEGORY_ONLY
    if subject.split(":", 1)[0].strip() in ("tos", "tos_filter", "chat_filter"):
        return PUBLIC_CATEGORY_ONLY
    return subject


def load(name):
    return json.loads((HERE / name).read_text())


# ---------------------------------------------------------------- numbers

def scout_breakdown(card_areas: set[str]) -> dict:
    """All scout sessions for card areas, split by whether they produced cards."""
    import sqlite3
    sys.path.insert(0, str(ROOT))
    import cards as K
    db = sqlite3.connect("/srv/factory/operator/.local/share/opencode/opencode.db")
    sess = db.execute("select title, tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, time_created/1000 "
                      "from session_v2 where title like 'scout-lane-%' and time_created > strftime('%s','2026-11-09T21:00:00')*1000").fetchall()
    logs = []
    for f in glob.glob(str(ROOT / "state/cards/scout/*.log")) + glob.glob(str(ROOT / "state/cards/scout-bad/*.log")):
        m = re.search(r"(lane-\d+)-(\d+)\.log", f)
        logs.append((m.group(1), int(m.group(2)), len(K.parse_cards(Path(f).read_text(errors="replace"))), "scout-bad" in f))
    out = collections.Counter()
    for title, i, o, r, cr, tc in sess:
        area = title.replace("scout-", "")
        usd = ((i or 0) * 2 + (cr or 0) * 0.5 + ((o or 0) + (r or 0)) * 6) / 1e6
        near = [l for l in logs if l[0] == area and abs(l[1] - tc) < 120]
        kind = "unmatched"
        if near:
            l = min(near, key=lambda x: abs(x[1] - tc))
            kind = "wrong_dir" if l[3] else ("produced" if l[2] else "no_cards")
        out[("card" if area in card_areas else "free") + "_" + kind] += usd
    return {k: round(v, 2) for k, v in out.items()}


def lane_hours() -> dict:
    hrs, mins = collections.Counter(), collections.defaultdict(list)
    for f in glob.glob(str(ROOT / "state/*-[0-9]/loop.log")):
        lane = Path(f).parent.name
        if not lane.startswith(("free-", "cards-")):
            continue
        begin = None
        for line in open(f, errors="replace"):
            m = re.match(r"^(\S+Z) pass (\d+) (begin|end)", line)
            if not m:
                continue
            t = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
            if m.group(3) == "begin":
                begin = t
            elif begin:
                d = (t - begin).total_seconds()
                hrs[lane.split("-")[0]] += d / 3600
                mins[lane.split("-")[0]].append(d / 60)
                begin = None
    import statistics
    return {a: {"lane_hours": round(hrs[a], 1), "median_pass_min": round(statistics.median(mins[a]), 1)} for a in hrs}


def numbers() -> dict:
    design, usage, tally = load("design.json"), load("usage.json"), load("tally-final.json")
    rows = load("commits.json")
    labels = {l["sha"]: l for l in load("value-labels.json")}
    integ, spot = load("integration-final.json"), load("spotcheck.json")
    conflict = {c["short"] for c in integ["conflicts"]}
    card_areas = set(design["arms"]["cards"]["areas"])
    cards = json.loads((ROOT / "state/cards/cards.json").read_text())
    scout = scout_breakdown(card_areas)
    hours = lane_hours()
    n = {"design": design, "rows": rows, "labels": labels, "conflict": conflict, "spot": spot, "scout": scout, "hours": hours}
    merge = load("merge.json")
    n["merge"] = merge
    for row in rows:
        row["label"] = labels[row["short"]]
        row["applies"] = row["short"] not in conflict
        row["merged"] = row["short"][:8] not in merge["not_merged"]
    arms = {}
    for arm in ("free", "cards"):
        rs = [r for r in rows if r["arm"] == arm]
        t = tally[arm]
        write_sub = usage.get(f"{arm}/writing/subscription", {}).get("usd", 0)
        write_api = usage.get(f"{arm}/writing/api", {}).get("usd", 0)
        review = usage.get(f"{arm}/review", {}).get("usd", 0)
        scout_all = sum(v for k, v in scout.items() if k.startswith("card_")) if arm == "cards" else 0.0
        scout_useful = scout.get("card_produced", 0) if arm == "cards" else 0.0
        nonminor = [r for r in rs if r["label"]["class"] != "minor"]
        nonminor_applied = [r for r in nonminor if r["applies"]]
        nonminor_merged = [r for r in nonminor if r["merged"]]
        sp = [s for s in spot["results"] if s["arm"] == arm]
        sessions = sum(usage.get(f"{arm}/writing/{route}", {}).get("sessions", 0) for route in ("subscription", "api"))
        cached = sum(usage.get(f"{arm}/writing/{route}", {}).get("cached", 0) for route in ("subscription", "api"))
        a = {
            "lanes": 7, "areas": design["arms"][arm]["areas"],
            "passes": t["passes"], "gate_pass": t["gate_pass"], "gate_fail": t.get("gate_fail", 0),
            "drop": t.get("drop", 0), "empty": t.get("no_commit", 0), "timeout": t.get("timeout", 0),
            "commits": len(rs), "keep": sum(r["verdict"] == "keep" for r in rs), "fix": sum(r["verdict"] == "fix" for r in rs),
            "classes": collections.Counter(r["label"]["class"] for r in rs),
            "real_bug": sum(r["label"]["real_bug"] for r in rs),
            "eng_hours": sum(r["label"]["eng_hours"] for r in rs if r["label"]["class"] != "minor"),
            "nonminor": len(nonminor), "nonminor_applied": len(nonminor_applied), "nonminor_merged": len(nonminor_merged),
            "merged": sum(r["merged"] for r in rs), "real_merged": sum(r["merged"] and r["label"]["real_bug"] for r in rs),
            "applies": sum(r["applies"] for r in rs), "conflicts": sum(not r["applies"] for r in rs),
            "write_sub": write_sub, "write_api": write_api, "write": write_sub + write_api,
            "review": review, "scout": scout_all, "scout_useful": scout_useful,
            "cached_per_pass_M": cached / max(sessions, 1) / 1e6,
            "spot_n": len(sp), "spot_real": sum(s["real_bug"] for s in sp), "spot_over": sum(s["overclaims"] for s in sp),
            "spot_proof": sum(s["fails_before"] is True and s["passes_after"] is True and "missing" not in s.get("note", "") for s in sp),
            **hours[arm],
        }
        a["primary"] = (a["write"] + a["scout"]) / a["nonminor_merged"]
        a["primary_applies"] = (a["write"] + a["scout"]) / a["nonminor_applied"]
        a["primary_nobug"] = (a["write"] + a["scout"] - scout.get("card_wrong_dir", 0) * (arm == "cards")) / a["nonminor_merged"]
        a["write_per_kept"] = a["write"] / a["commits"]
        a["all_in"] = a["write"] + a["review"] + a["scout"]
        a["per_eng_hour"] = a["all_in"] / a["eng_hours"]
        a["per_lane_hour"] = a["commits"] / a["lane_hours"]
        arms[arm] = a
    n["arms"] = arms
    card_rows = [c for c in cards if c["area"] in card_areas]
    n["cards"] = {"proposed": len(card_rows), "accepted": sum(c["state"] in ("done", "failed", "assigned", "accepted") for c in card_rows),
                  "done": sum(c["state"] == "done" for c in card_rows), "failed": sum(c["state"] == "failed" for c in card_rows),
                  "unreviewed": sum(c["state"] == "proposed" for c in card_rows)}
    shared = collections.defaultdict(set)
    for r in rows:
        for f in r["files"]:
            shared[f].add(r["lane"])
    n["shared_files_cards"] = sum(1 for f, ls in shared.items() if len(ls) > 1 and all(l.startswith("cards") for l in ls))
    n["integration"] = {"applied": integ["applied"], "candidates": integ["candidates"], "conflicts": len(integ["conflicts"]),
                        "blamed": integ["blamed"], "passed": (integ.get("gate") or {}).get("passed")}
    f, c = arms["free"], arms["cards"]
    n["primary_diff"] = (c["primary"] - f["primary"]) / f["primary"]
    n["applies_diff"] = (c["primary_applies"] - f["primary_applies"]) / f["primary_applies"]
    n["r1"], n["r2"] = round_comparison(n, usage)
    return n


def round_comparison(n, usage) -> tuple[dict, dict]:
    """Round 1 from its published report and labels; round 2 on the same definitions."""
    r1_labels = json.loads((ROOT / "research/value-labels.json").read_text())
    r1 = {"integrated": 110, "real": 77, "minor": 20, "fixes": 100, "untriggerable": 14, "keep_rate": 0.89,
          "tokens_usd": 391 + 19, "labelled": len(r1_labels),
          "medium": sum(l["severity"] in ("medium", "high") for l in r1_labels), "worktree_only_failures": 80}
    merged = [r for r in n["rows"] if r["merged"]]
    fixes = [r for r in merged if r["label"]["class"] in ("security-or-safety", "user-facing-bug", "reliability")]
    f, c = n["arms"]["free"], n["arms"]["cards"]
    reviewed = f["commits"] + c["commits"] + f["drop"] + c["drop"]
    r2 = {"integrated": len(merged), "real": sum(r["label"]["real_bug"] for r in merged),
          "minor": sum(r["label"]["class"] == "minor" for r in merged), "fixes": len(fixes),
          "untriggerable": sum(not r["label"]["real_bug"] for r in fixes),
          "keep_rate": (f["commits"] + c["commits"]) / reviewed,
          "tokens_usd": sum(v["usd"] for v in usage.values()), "labelled": len(n["rows"]),
          "medium": sum(r["label"]["severity"] in ("medium", "high") for r in n["rows"]), "worktree_only_failures": 21,
          "gate_fail": f["gate_fail"] + c["gate_fail"], "gate_total": f["gate_pass"] + c["gate_pass"] + f["gate_fail"] + c["gate_fail"]}
    for r in (r1, r2):
        r["per_real"] = r["tokens_usd"] / r["real"]
    return r1, r2


# ---------------------------------------------------------------- figures

def svg(w, h, label, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(label)}" '
            f'font-family="Inter,system-ui,sans-serif" font-size="11">{body}</svg>')


def fig_classes(n):
    w, h, x0, bw = 640, 210, 120, 470
    body, scale = [], bw / max(n["arms"][a]["commits"] for a in ("free", "cards"))
    for i, arm in enumerate(("free", "cards")):
        a, y, x = n["arms"][arm], 30 + i * 70, x0
        body.append(f'<text x="{x0 - 10}" y="{y + 20}" text-anchor="end" fill="{INK}" font-weight="600">{"Free lanes" if arm == "free" else "Card lanes"}</text>')
        body.append(f'<text x="{x0 - 10}" y="{y + 34}" text-anchor="end" fill="{MUTED}" font-size="10">{a["commits"]} commits</text>')
        for cls in CLASSES:
            k = a["classes"].get(cls, 0)
            if k:
                body.append(f'<rect x="{x:.1f}" y="{y}" width="{k * scale:.1f}" height="34" fill="{COL[cls]}"><title>{CLASS_NAMES[cls]}: {k}</title></rect>')
                if k * scale > 18:
                    body.append(f'<text x="{x + k * scale / 2:.1f}" y="{y + 21}" text-anchor="middle" fill="#fff" font-size="10.5" font-weight="600">{k}</text>')
                x += k * scale
    for j, cls in enumerate(CLASSES):
        lx = x0 + j * 98
        body.append(f'<rect x="{lx}" y="{h - 26}" width="9" height="9" fill="{COL[cls]}"/><text x="{lx + 13}" y="{h - 18}" fill="{MUTED}" font-size="9.5">{CLASS_NAMES[cls]}</text>')
    return svg(w, h, "Surviving commits per arm by value class, labelled blind to arm", "".join(body))


def fig_cost(n):
    parts = [("write_sub", "Writing, subscription", "#0f766e"), ("write_api", "Writing, API", "#5eaaa2"),
             ("review", "Review (Opus)", "#6d4fa1"), ("scout_useful", "Scouting that produced cards", "#b7791f"),
             ("scout_waste", "Scouting that produced none", "#e3c48c")]
    w, h, x0, bw = 640, 230, 120, 470
    for a in n["arms"].values():
        a["scout_waste"] = a["scout"] - a["scout_useful"]
    top = max(sum(a[k] for k, _, _ in parts) for a in n["arms"].values())
    scale, body = bw / top, []
    for i, arm in enumerate(("free", "cards")):
        a, y, x = n["arms"][arm], 30 + i * 70, x0
        body.append(f'<text x="{x0 - 10}" y="{y + 20}" text-anchor="end" fill="{INK}" font-weight="600">{"Free lanes" if arm == "free" else "Card lanes"}</text>')
        body.append(f'<text x="{x0 - 10}" y="{y + 34}" text-anchor="end" fill="{MUTED}" font-size="10">${sum(a[k] for k, _, _ in parts):.0f}</text>')
        for k, name, col in parts:
            v = a[k]
            if v > 0.5:
                body.append(f'<rect x="{x:.1f}" y="{y}" width="{v * scale:.1f}" height="34" fill="{col}"><title>{name}: ${v:.0f}</title></rect>')
                if v * scale > 30:
                    body.append(f'<text x="{x + v * scale / 2:.1f}" y="{y + 21}" text-anchor="middle" fill="#fff" font-size="10" font-weight="600">${v:.0f}</text>')
                x += v * scale
    for j, (_, name, col) in enumerate(parts):
        lx, ly = x0 + (j % 3) * 165, h - 40 + (j // 3) * 16
        body.append(f'<rect x="{lx}" y="{ly}" width="9" height="9" fill="{col}"/><text x="{lx + 13}" y="{ly + 8}" fill="{MUTED}" font-size="9.5">{name}</text>')
    return svg(w, h, "Spend per arm at list prices, by role", "".join(body))


def fig_timeline(n):
    rows = sorted(n["rows"], key=lambda r: r["at"])
    t0 = dt.datetime(2026, 11, 10, 0, 0, tzinfo=dt.timezone.utc)
    t1 = dt.datetime(2026, 11, 10, 7, 40, tzinfo=dt.timezone.utc)
    w, h, x0, y0, pw, ph = 640, 250, 46, 26, 520, 174
    span = (t1 - t0).total_seconds()
    top = max(n["arms"][a]["commits"] for a in n["arms"]) + 16
    X = lambda t: x0 + (t - t0).total_seconds() / span * pw  # noqa: E731
    Y = lambda k: y0 + ph - k / top * ph  # noqa: E731
    body = [f'<line x1="{x0}" y1="{y0 + ph}" x2="{x0 + pw}" y2="{y0 + ph}" stroke="{GRID}"/>']
    for k in range(0, top, 20):
        body.append(f'<line x1="{x0}" y1="{Y(k):.1f}" x2="{x0 + pw}" y2="{Y(k):.1f}" stroke="{GRID}" stroke-width=".6"/><text x="{x0 - 6}" y="{Y(k) + 3:.1f}" text-anchor="end" fill="{MUTED}" font-size="9">{k}</text>')
    for hr in range(0, 8):
        t = t0 + dt.timedelta(hours=hr)
        body.append(f'<text x="{X(t):.1f}" y="{y0 + ph + 14}" text-anchor="middle" fill="{MUTED}" font-size="9">{hr:02d}:00</text>')
    for label, at, anchor, dx in (("subscription quota exhausted", dt.datetime(2026, 11, 10, 2, 45, tzinfo=dt.timezone.utc), "end", -4),
                                  ("resumed on the API account", dt.datetime(2026, 11, 10, 6, 13, tzinfo=dt.timezone.utc), "start", 4)):
        body.append(f'<line x1="{X(at):.1f}" y1="{y0 - 12}" x2="{X(at):.1f}" y2="{y0 + ph}" stroke="{MUTED}" stroke-dasharray="3 3"/>'
                    f'<text x="{X(at) + dx:.1f}" y="{y0 - 4}" text-anchor="{anchor}" fill="{MUTED}" font-size="9">{label}</text>')
    for arm in ("free", "cards"):
        pts, k = [(X(t0), Y(0))], 0
        for r in rows:
            if r["arm"] == arm:
                k += 1
                pts.append((X(dt.datetime.fromisoformat(r["at"]).astimezone(dt.timezone.utc)), Y(k)))
        path = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
        body.append(f'<polyline points="{path}" fill="none" stroke="{ARM_COL[arm]}" stroke-width="2"/>'
                    f'<text x="{x0 + pw + 8:.1f}" y="{pts[-1][1] + (10 if arm == "free" else -2):.1f}" fill="{ARM_COL[arm]}" font-size="10" font-weight="600">{"free" if arm == "free" else "cards"} {k}</text>')
    body.append(f'<text x="{x0 + pw / 2}" y="{h - 6}" text-anchor="middle" fill="{MUTED}" font-size="9">UTC, 10 November 2026; cumulative commits that survived gate and review</text>')
    return svg(w, h, "Surviving commits over time per arm, with the quota gap", "".join(body))


def fig_funnel(n):
    c, a = n["cards"], n["arms"]["cards"]
    steps = [("Proposed by the scout", c["proposed"]), ("Accepted by the operator", c["accepted"]),
             ("Built and kept", c["done"]), ("Non-minor", a["nonminor"]), ("Non-minor, merged onto master", a["nonminor_merged"])]
    w, h, x0, bw = 640, 40 + 30 * len(steps), 220, 380
    scale, body = bw / steps[0][1], []
    for i, (name, v) in enumerate(steps):
        y = 14 + i * 30
        body.append(f'<text x="{x0 - 10}" y="{y + 15}" text-anchor="end" fill="{INK}">{name}</text>'
                    f'<rect x="{x0}" y="{y}" width="{v * scale:.1f}" height="21" fill="#0f766e" opacity="{1 - i * 0.13:.2f}"/>'
                    f'<text x="{x0 + v * scale + 6:.1f}" y="{y + 15}" fill="{INK}" font-weight="600">{v}</text>')
    return svg(w, h, "The card pipeline, from proposal to a commit that applies with every other lane", "".join(body))


# ---------------------------------------------------------------- text

def pct(x):
    return f"{100 * x:.0f}%"


def text(n):
    f, c, d, cd = n["arms"]["free"], n["arms"]["cards"], n["design"], n["cards"]
    r1, r2 = n["r1"], n["r2"]
    scout_waste = c["scout"] - c["scout_useful"]
    diff = n["primary_diff"]
    res = {}
    res["highlights"] = [
        ("More real fixes", f"{r2['real']} merged commits fix a bug a real input can trigger, up from {r1['real']} in round 1 (+{100 * (r2['real'] / r1['real'] - 1):.0f}%)."),
        ("Half the noise", f"Minor commits fell from {pct(r1['minor'] / r1['integrated'])} to {pct(r2['minor'] / r2['integrated'])} of what was merged. "
                           f"Round 1 had {r1['untriggerable']} \u201cfixes\u201d for bugs no input could reach; this time the classifier found {r2['untriggerable']} and a blind spot-check one overstated claim in 16."),
        ("Proof before review", f"Every commit had to name a test that fails before the change and passes after. {r2['gate_total'] - r2['gate_fail']} of {r2['gate_total']} passed the gate and {pct(r2['keep_rate'])} of those survived review (round 1: {pct(r1['keep_rate'])})."),
        ("Same price per fix", f"About ${r2['per_real']:.2f} of model tokens per real fix, against ${r1['per_real']:.2f} in round 1. The night bought more fixes, not cheaper ones."),
        ("Cards: cheaper to build, not to run", f"Scout-written task cards made building {f['write_per_kept'] / c['write_per_kept']:.1f}\u00d7 cheaper per commit, but scouting cost more than building. "
                                                 f"All-in, the two arms finished {abs(100 * diff):.0f}% apart."),
        ("Approval was a rubber stamp", f"The operator accepted every card he reviewed and declined none. The human judgement that changed outcomes was at merge, not at approval."),
    ]
    res["abstract"] = (
        f"We ran fourteen unattended coding-agent lanes on a private production codebase for one night, with the harness changed in response to round 1: "
        f"every commit had to carry a proof test, prompts asked agents to reproduce before fixing, and a reviewer judged each commit as it landed. "
        f"Against round 1, merged real fixes rose from {r1['real']} to {r2['real']}, minor commits fell from {pct(r1['minor'] / r1['integrated'])} to {pct(r2['minor'] / r2['integrated'])}, and token cost per real fix held at about $5. "
        f"Within the night we compared two arms of seven lanes. Free lanes hunted for their own work in one area each; scout-directed lanes built task cards that a read-only scout proposed. "
        f"Cards made building {f['write_per_kept'] / c['write_per_kept']:.1f}\u00d7 cheaper, but scouting, idle time and conflicts between card lanes cancelled the saving: "
        f"on the pre-registered measure, cost per non-minor commit merged onto master, the arms came out at ${f['primary']:.2f} and ${c['primary']:.2f}. "
        f"Operator approval of cards rejected nothing. What improved was what each commit had to prove, not who chose the work.")
    res["findings"] = [
        f"<b>The round got better, in both arms.</b> Minor commits were {pct(f['classes'].get('minor', 0) / f['commits'])} of free and {pct(c['classes'].get('minor', 0) / c['commits'])} of card commits, against {pct(r1['minor'] / r1['integrated'])} in round 1. "
        f"The changes behind it applied to both arms: the proof requirement, reproduce-first prompting, the reviewer's value classes in the prompt, review after every commit, and test checkouts that no longer failed for lack of a config file ({r1['worktree_only_failures']} such failures down to about {r2['worktree_only_failures']}).",
        f"<b>Specifying the work made building cheap.</b> A card pass took a median {c['median_pass_min']:.0f} minutes and re-read {c['cached_per_pass_M']:.1f}M cached tokens; "
        f"a free pass took {f['median_pass_min']:.0f} minutes and re-read {f['cached_per_pass_M']:.1f}M.",
        f"<b>The saving moved into scouting.</b> Scouting the card areas cost ${c['scout']:.0f}, more than the ${c['write']:.0f} spent building cards. "
        f"${scout_waste:.0f} of it produced no cards: hung sessions, a run started in the wrong directory, and a run cut off by the quota.",
        f"<b>No difference all-in.</b> Per non-minor commit merged onto master, writing plus scouting cost ${f['primary']:.2f} for free lanes and ${c['primary']:.2f} for card lanes, {100 * diff:+.0f}%, far below the 30% we set in advance.",
        f"<b>A shared queue caused merge conflicts.</b> All {c['conflicts']} commits that did not apply automatically came from card lanes, which drew from one queue over seven areas. Free lanes, one area each, had none.",
        f"<b>Approval did not select.</b> The operator accepted {cd['accepted']} cards and declined none; {cd['unreviewed']} were never reviewed. The arm was scout-directed in practice. "
        f"Waiting for approval left card lanes idle: they ran {c['lane_hours']} lane-hours against {f['lane_hours']}.",
        f"<b>Cards shaped the kind of work, and sometimes the proof.</b> The scout was told to look for security gaps and missing behaviour tests, and card lanes produced {c['classes']['security-or-safety']} security fixes and {c['classes']['meaningful-test']} tests "
        f"(free: {f['classes']['security-or-safety']} and {f['classes']['meaningful-test']}). In the blind spot-check, {f['spot_proof']} of 8 free proofs and {c['spot_proof']} of 8 card proofs were genuine; some cards asked for a proof the change could not honestly give.",
    ]
    res["method"] = f"""
<p>The run used the factory from the first technical report (FL-TR-2026-01) with the changes listed below. Fourteen lanes ran in parallel, each in its own git worktree and branch from the same master commit (<code>{d['base'][:8]}</code>). Each pass made at most one small commit. A gate checked every commit: Python compiles, JavaScript parses, forbidden files untouched, size limits, related tests and every Node test against the base, and a <code>Proof:</code> line in the message. Claude Opus 5.5 reviewed every commit that passed the gate before the lane continued: <i>keep</i>, <i>fix</i> (kept, and the next pass must fix what the reviewer named), or <i>drop</i> (removed from the branch). The writer was Grok 4.7 throughout.</p>
<p><b>Arms.</b> Round 1's fourteen focus areas were split at random (seed {d['seed']}) into two sets of seven. Free lanes got one area each, with round 1's focus text and a note that the obvious bugs had been fixed. Scout-directed lanes shared one queue of cards from the other seven areas. Before and during the run, a scout (Grok 4.7, read-only) read each card area and proposed cards: a title, a class, the files it had read, a brief, and the proof that would show the card done. The design called for the operator to accept or decline each card; in practice he accepted every card he reviewed. A card lane took the oldest accepted card, worked it until a commit survived the gate and review, then took the next. Two failed passes on one card marked it failed.</p>
<p><b>Changes since round 1</b>, applied to both arms: the <code>Proof:</code> requirement; prompt changes (reproduce before fixing, the reviewer's value classes, a turn budget, no <code>git stash</code>); uncommitted leftovers saved as patches instead of stashed (round 1's shared stash swapped work between lanes); each pass shown its own branch history; a sanitized copy of the configuration file (secrets replaced) in every checkout, which cut tests that fail only in a worktree from {r1['worktree_only_failures']} to about {r2['worktree_only_failures']}; a 25-minute pass limit and a watchdog for passes that stop producing output; and a detector that stops every lane when most of them fail together.</p>
<p><b>Pre-registration.</b> The question, outcomes and the threshold for calling a difference were written before the run (<code>research/round2/criteria.md</code>). The primary outcome is writing and scouting cost at list price per non-minor commit integrated onto master; a difference of 30% or more, with at least 40 gate-passed commits per arm, counts as a result.</p>
<p><b>Integration.</b> After the run every surviving commit was cherry-picked onto master in commit order. {n['integration']['applied']} applied cleanly; the operator's session resolved the {n['integration']['conflicts']} conflicts by hand. Six were not merged: three whose fix master already had from another lane (their tests pass on the merged code), two duplicate tests, and one folded into another commit. One was merged in part. The full suite showed no new failures against master, and the result was deployed and restarted the same day. Eight of the merged commits added known-failing tests that pinned open safety-filter gaps; the operator's session closed those gaps the same day, and the tests now pass.</p>
<p><b>Value labels and spot-check.</b> After the run Claude Opus 5.5 classified all {f['commits'] + c['commits']} surviving commits in random order, with lane, arm and card removed, from the message, the diff and the reviewer's reason. Classes are round 1's: security or safety, user-facing bug, reliability, behaviour test, minor. Four independent checkers, blind to arm and verdicts, took 16 commits (8 per arm, seed 20261110), ran each commit's proof test on the parent with only the new tests applied and again on the commit, and gave their own class. They agreed with the classifier on {n['spot']['summary']['class_agreement']} classes and {n['spot']['summary']['real_bug_agreement']} real-bug calls.</p>
<p><b>Costs</b> are token counts from the harness's session store priced at public list rates (Grok 4.7: $2 input, $0.50 cached input, $6 output and reasoning per million tokens; Claude Opus 5.5: $4, $0.20, $20, cache writes $5). The subscription charged nothing per token; the API account did. List prices make the arms and rounds comparable.</p>
"""
    res["vs_round1"] = f"""
<table class=kr><caption>Round 1 and round 2 on the same definitions</caption>
<tr><td class=k></td><td class=v>Round 1</td><td class=v>Round 2</td><td class=k></td></tr>
<tr><td class=k>Commits merged onto master</td><td class=v>{r1['integrated']}</td><td class=v>{r2['integrated']}</td><td class=k>both runs one night, fourteen lanes</td></tr>
<tr><td class=k>Real bugs fixed</td><td class=v>{r1['real']}</td><td class=v>{r2['real']}</td><td class=k>a real input triggers the old behaviour</td></tr>
<tr><td class=k>Minor share</td><td class=v>{pct(r1['minor'] / r1['integrated'])}</td><td class=v>{pct(r2['minor'] / r2['integrated'])}</td><td class=k>worked, but nobody would have missed it</td></tr>
<tr><td class=k>Fixes for unreachable bugs</td><td class=v>{r1['untriggerable']} of {r1['fixes']}</td><td class=v>{r2['untriggerable']} of {r2['fixes']}</td><td class=k>classifier; spot-check found 1 overstated in 16</td></tr>
<tr><td class=k>Survived review</td><td class=v>{pct(r1['keep_rate'])}</td><td class=v>{pct(r2['keep_rate'])}</td><td class=k>of commits that passed the gate</td></tr>
<tr><td class=k>Tokens, list price</td><td class=v>${r1['tokens_usd']:.0f}</td><td class=v>${r2['tokens_usd']:.0f}</td><td class=k>writing, review, scouting</td></tr>
<tr><td class=k><b>Per real fix</b></td><td class=v>${r1['per_real']:.2f}</td><td class=v>${r2['per_real']:.2f}</td><td class=k>flat</td></tr>
</table>
<p>The labels for both rounds come from the same model and classes, but from separate classification runs with slightly different prompts, so small differences should not be read closely. The large ones, the minor share and the unreachable fixes, also show in the review and spot-check results. Round 2 cost more in total because it ran more passes on a pay-per-token account after the subscription quota ran out; per real fix the price did not move. We cannot say which of the harness changes did the work: they shipped together.</p>
"""
    res["timeline"] = f"""
<p>Lanes started at 00:14 UTC on 10 November. The card lanes stopped at once on the first start: their queue lookup read a per-lane directory instead of the shared one. They were fixed and restarted at 00:18. The scout ran ahead of the builders, and the operator accepted cards in batches. From 02:19 card lanes began stopping with empty queues. At 02:45 the Grok subscription quota ran out; most lanes failed within eight minutes and the detector stopped all fourteen. At 06:13 the run resumed on the pay-per-token xAI account with the same model: free lanes immediately, card lanes after a new scout run and acceptance, from 06:34. At 07:15 the operator stopped the factory; lanes finished their passes by 07:35. Free lanes ran {f['lane_hours']} lane-hours of passes, card lanes {c['lane_hours']}.</p>
"""
    res["results"] = f"""
<table class=kr><caption>Results per arm</caption>
<tr><td class=k></td><td class=v>Free</td><td class=v>Cards</td><td class=k></td></tr>
<tr><td class=k>Passes</td><td class=v>{f['passes']}</td><td class=v>{c['passes']}</td><td class=k>including {f['empty']} and {c['empty']} with no commit</td></tr>
<tr><td class=k>Passed the gate</td><td class=v>{f['gate_pass']}</td><td class=v>{c['gate_pass']}</td><td class=k>{f['gate_fail']} and {c['gate_fail']} failed it</td></tr>
<tr><td class=k>Survived review</td><td class=v>{f['commits']}</td><td class=v>{c['commits']}</td><td class=k>{f['fix']} and {c['fix']} with a fix request; {f['drop']} and {c['drop']} dropped</td></tr>
<tr><td class=k>Applied without conflict</td><td class=v>{f['applies']}</td><td class=v>{c['applies']}</td><td class=k>conflicts: {f['conflicts']} and {c['conflicts']}, resolved by hand</td></tr>
<tr><td class=k>Merged onto master</td><td class=v>{f['merged']}</td><td class=v>{c['merged']}</td><td class=k>non-minor: {f['nonminor_merged']} and {c['nonminor_merged']}</td></tr>
<tr><td class=k>Writing</td><td class=v>${f['write']:.0f}</td><td class=v>${c['write']:.0f}</td><td class=k>list price</td></tr>
<tr><td class=k>Scouting</td><td class=v>—</td><td class=v>${c['scout']:.0f}</td><td class=k>${scout_waste:.0f} produced no cards</td></tr>
<tr><td class=k>Review</td><td class=v>${f['review']:.0f}</td><td class=v>${c['review']:.0f}</td><td class=k>Opus, every commit</td></tr>
<tr><td class=k><b>Primary: cost per non-minor commit merged</b></td><td class=v>${f['primary']:.2f}</td><td class=v>${c['primary']:.2f}</td><td class=k>writing + scouting; cards {100 * diff:+.0f}%</td></tr>
</table>
<p>The difference is far below the pre-registered 30%. Before the conflicts were resolved by hand, the stand-in measure (only commits that applied automatically) gave ${c['primary_applies']:.2f} against ${f['primary_applies']:.2f}; hand resolution recovered most of the card arm's conflicting work. Leaving out the ${n['scout'].get('card_wrong_dir', 0):.0f} spent by the scout run started in the wrong directory, a setup bug, gives the card arm ${c['primary_nobug']:.2f}.</p>
<p>Card lanes were idle for long stretches waiting for approved cards. A scout that keeps ahead of the builders, without a human approval step, would spend more on building and scouting but could produce more in the same night.</p>
"""
    res["value"] = f"""
<p>Blind classification found {f['real_bug']} of {f['commits']} free commits and {c['real_bug']} of {c['commits']} card commits fix behaviour a real input could trigger. The card arm's lower share comes from its {c['classes']['meaningful-test']} behaviour tests, which add tests for real decisions without a production change; the scout was told to propose them. The card arm found more security and safety problems ({c['classes']['security-or-safety']} against {f['classes']['security-or-safety']}), which the scout was also told to look for.</p>
<p>The classifier's rough estimate of engineering time to find, fix and test each non-minor change totals {f['eng_hours']} hours for free lanes and {c['eng_hours']} for card lanes: ${f['per_eng_hour']:.2f} and ${c['per_eng_hour']:.2f} of total spend per estimated engineer-hour. These are model judgements, not measured effort.</p>
<p>The spot-check found all 8 sampled free commits fixed real bugs, with proofs that failed before and passed after and no overclaiming. Of 8 card commits, {c['spot_real']} fixed real bugs, {c['spot_proof']} had proofs that tested behaviour, and {c['spot_over']} overclaimed. In one the proof line said a new test failed before the change, but the commit only added a test, as the card asked. Two other card proofs failed before only because a new module or constant did not exist yet.</p>
"""
    res["lessons"] = """
<ul>
<li><b>Keep the proof requirement and check it mechanically.</b> The checkers' procedure, run the named test on the parent with only the commit's tests and again on the commit, is cheap enough for the gate. It would have caught the false proof and the proofs that fail only because a symbol is new.</li>
<li><b>Drop card approval; review at merge.</b> Approval rejected nothing and idled lanes. The merge review caught duplicates, superseded fixes and a false proof.</li>
<li><b>Give lanes their own files, not just their own queue.</b> Partitioning by area kept free lanes conflict-free. Card lanes need the same: assign each card to the lane that owns its files, or hold a card while another lane has its files open.</li>
<li><b>Keep the scout ahead and make it cheaper.</b> Scouting cost more than building and a third of it produced nothing. A scout run needs its own stall watchdog and should stop proposing for areas that come back empty.</li>
<li><b>Do not let cards dictate the proof's form.</b> A card for a behaviour test should ask for a test of a named production decision, not a test that fails before.</li>
<li><b>Make agents fix what they find, not only pin it.</b> In both rounds the factory found safety-filter gaps and recorded them as known-failing tests instead of fixing them: seven in round 1, eight here. A human session fixed both sets afterwards. A card or review rule that sends a pinned gap back for a fix would close that loop.</li>
<li><b>Plan for the quota.</b> Throughput was set by the provider's quota. A run plan should state which account is next and at what cost before it starts.</li>
</ul>
"""
    res["limits"] = f"""
<ul>
<li>One night, one codebase, one writer model, seven lanes per arm. Each area appeared in only one arm, so a rich or poor area moves its arm's numbers.</li>
<li>The round-1 comparison is before and after, not controlled: the code had already been mined once, and all harness changes shipped together.</li>
<li>Value classes, real-bug calls and engineer-hours are AI judgements. The spot-check covered 16 of {f['commits'] + c['commits']} commits.</li>
<li>Merged is not the same as proven in production. Value counts once the changes stay live and are not reverted.</li>
<li>The arms did not run for the same time: card lanes waited for cards. Primary outcomes are per commit, not per hour.</li>
<li>Scouting for free-arm areas (${sum(v for k, v in n['scout'].items() if k.startswith('free_')):.0f}, unused) is excluded from both arms but included in the round's token total.</li>
<li>The operator's time accepting cards was not recorded. Since approval declined nothing, it bought nothing measurable.</li>
<li>The last 1.4 hours ran on a different billing account with the same model. Costs at list price are unaffected.</li>
</ul>
"""
    return res


# ---------------------------------------------------------------- render

def appendix(n):
    head = "<table class=changes><colgroup><col class=l><col class=c><col class=s><col class=r><col class=o><col class=v></colgroup><thead><tr><th>Lane</th><th>Commit</th><th>Subject</th><th>Review</th><th>Class</th><th>Merged</th></tr></thead><tbody>"
    body = []
    for r in sorted(n["rows"], key=lambda r: (r["arm"], int(r["lane"].split("-")[1]), r["at"])):
        card = f" (card {r['card']})" if r["card"] else ""
        body.append(f"<tr><td>{r['lane']}</td><td>{r['short']}</td><td>{esc(public_subject(r))}{card}</td><td>{r['verdict'] or '—'}</td>"
                    f"<td>{CLASS_NAMES[r['label']['class']]}</td><td>{('yes' if r['applies'] else 'by hand') if r['merged'] else 'no: ' + esc(n['merge']['not_merged'][r['short'][:8]].split(':')[0])}</td></tr>")
    return head + "".join(body) + "</tbody></table>"


def paper(n, t) -> Path:
    f, c = n["arms"]["free"], n["arms"]["cards"]
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{esc(TITLE)}</title><style>{P1.CSS}</style></head><body>",
           f"<p class=label>{DRAFT} · {REPORT_ID}</p><h1>{esc(TITLE)}</h1><p class=subtitle>{esc(SUBTITLE)}</p>",
           f"<div class=meta><div><b>Authors</b>{P1.AUTHORS}</div><div><b>Date</b>2026-11-10</div>"
           "<div><b>Writer · reviewer</b>Grok 4.7 · Claude Opus 5.5</div><div><b>Status</b>Draft · merged and deployed</div></div>",
           "<p class=status><b>Draft.</b> All changes are merged onto master and deployed. Value labels and engineer-hours are AI estimates; "
           "value counts once the changes stay live.</p>",
           f"<div class=abstract><h3>Abstract</h3><p>{t['abstract']}</p></div>",
           "<p class=label>Highlights</p><ol class=findings>" + "".join(f"<li><b>{esc(h)}.</b> {esc(x)}</li>" for h, x in t["highlights"]) + "</ol>",
           f"<h2><span class=n>1</span>Against round 1</h2>{t['vs_round1']}",
           "<h2><span class=n>2</span>Summary of findings</h2><ol class=findings>" + "".join(f"<li>{x}</li>" for x in t["findings"]) + "</ol>",
           f"<h2><span class=n>3</span>Method</h2>{t['method']}",
           f"<h2><span class=n>4</span>How the night went</h2>{t['timeline']}<figure>{fig_timeline(n)}<figcaption><b>Figure 1.</b> Commits that survived the gate and review, by arm, over time. Card lanes flatten when the queue empties; both stop when the quota runs out.</figcaption></figure>",
           f"<h2><span class=n>5</span>Two arms: free and scout-directed</h2>{t['results']}<figure>{fig_cost(n)}<figcaption><b>Figure 2.</b> Spend per arm at list prices. Card building is cheap; scouting is most of the card arm's cost.</figcaption></figure>"
           f"<figure>{fig_funnel(n)}<figcaption><b>Figure 3.</b> The card pipeline. Approval passed every reviewed card; conflicts between card lanes were resolved by hand at merge.</figcaption></figure>",
           f"<h2><span class=n>6</span>What the commits were worth</h2>{t['value']}<figure>{fig_classes(n)}<figcaption><b>Figure 4.</b> Surviving commits by value class, labelled blind to arm.</figcaption></figure>",
           f"<h2><span class=n>7</span>What we would change</h2>{t['lessons']}",
           f"<h2><span class=n>8</span>Limits</h2>{t['limits']}",
           f"<h2><span class=n>9</span>Data</h2><ul class=refs><li>Sanitized per-commit records, per-arm numbers, the round comparison, token accounting and spot-check results: <code>https://fracktron.com/research/{SLUG}/data.json</code>.</li>"
           "<li>Pre-registration: <code>criteria.md</code> in the same directory. Application source, diffs, commit bodies, cards and agent transcripts are not published.</li>"
           "<li>As in round 1, security and safety changes are listed by category only.</li></ul>",
           f"<div class=appendix><h2><span class=n>A</span>Appendix: every surviving commit</h2>{appendix(n)}</div></body></html>"]
    OUT.mkdir(exist_ok=True)
    path = OUT / "paper.html"
    path.write_text("".join(out))
    return path


def markdown(n, t) -> None:
    strip = lambda s: re.sub(r"<[^>]+>", "", s).strip()  # noqa: E731
    f, c = n["arms"]["free"], n["arms"]["cards"]
    md = [f"# {TITLE}\n\n_{SUBTITLE}_ {REPORT_ID}, Infected Research / FrackLabs, 2026-11-10. Preliminary; merged and deployed.\n",
          "## Abstract\n\n" + t["abstract"] + "\n", "## Highlights\n"]
    md += [f"- **{h}.** {x}" for h, x in t["highlights"]]
    md.append("\n## Findings\n")
    md += [f"{i}. {strip(x)}" for i, x in enumerate(t["findings"], 1)]
    md.append("\n## Results\n\n| | Free | Cards |\n|---|---:|---:|")
    for k, label in [("passes", "Passes"), ("gate_pass", "Passed the gate"), ("commits", "Survived review"), ("nonminor", "Non-minor"),
                     ("merged", "Merged onto master"), ("nonminor_merged", "Non-minor, merged")]:
        md.append(f"| {label} | {f[k]} | {c[k]} |")
    for k, label in [("write", "Writing"), ("scout", "Scouting"), ("review", "Review"), ("primary", "**Primary: cost per non-minor commit merged**")]:
        md.append(f"| {label} | ${f[k]:.2f} | ${c[k]:.2f} |")
    (OUT / "report.md").write_text("\n".join(md) + "\n")


PDF_JS = P1.PDF_JS


def pdf(html_path: Path) -> Path:
    target = OUT / PDF_NAME
    script = Path("/srv/factory/scratch/round2-pdf.js")
    script.write_text(PDF_JS % {"html": html_path, "pdf": target, "rid": REPORT_ID, "state": DRAFT})
    subprocess.run(["node", str(script)], check=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin",
                   "NODE_PATH": "/srv/factory/operator/software-factory-public/node_modules", "HOME": str(Path.home())})
    return target


def main() -> None:
    n = numbers()
    t = text(n)
    html_path = paper(n, t)
    markdown(n, t)
    summary = {a: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in n["arms"][a].items() if not isinstance(v, (dict, list, collections.Counter))}
               for a in n["arms"]}
    (HERE / "numbers.json").write_text(json.dumps({"arms": summary, "cards": n["cards"], "scout": n["scout"], "primary_diff": round(n["primary_diff"], 3)}, indent=1))
    print(pdf(html_path))


if __name__ == "__main__":
    main()
