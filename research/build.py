"""Build the research page (page/index.html) and its Markdown copy (page/report.md) from the run's data.

Inputs (all in research/): data.json (collect.py), ledger.json, spotcheck.json, value-labels.json,
costs.json, status.json (sign-off and production state), conclusion.md (optional), and
../state/final-suite.json.
"""

from __future__ import annotations

import collections
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "page"
STATE = HERE.parent / "state"

KEEP, FIX, DROP, OTHER = "#3f8f5f", "#d29a2b", "#c4513f", "#8a8f98"
INK, MUTED, GRID = "#1d2330", "#5d6472", "#e6e8ec"
CLASS_COLOURS = {"security-or-safety": "#2f6f9f", "user-facing-bug": KEEP, "reliability": "#6a8f3f",
                 "meaningful-test": "#8a6fb0", "minor": OTHER}
CLASS_NAMES = {"security-or-safety": "Security or safety", "user-facing-bug": "User-facing bug", "reliability": "Reliability",
               "meaningful-test": "Test of real behaviour", "minor": "Minor"}


def load(name, default=None):
    path = name if isinstance(name, Path) else HERE / name
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def esc(value) -> str:
    return html.escape(str(value))


def money(phase: dict, prices: dict) -> float:
    p = prices[phase["model"]]
    if phase["model"].startswith("grok"):
        return (phase["input"] * p["input"] + phase["cached"] * p["cached_input"]
                + (phase["output"] + phase["reasoning"]) * p["output"]) / 1e6
    return (phase["input"] * p["input"] + phase["cached"] * p["cache_read"] + phase["cache_write"] * p["cache_write"]
            + (phase["output"] + phase["reasoning"]) * p["output"]) / 1e6


# ---------- numbers ----------

def numbers() -> dict:
    data, ledger, spot = load("data.json"), load("ledger.json"), load("spotcheck.json")
    labels = {r["sha"]: r for r in load("value-labels.json", [])}
    costs, status = load("costs.json"), load("status.json", {})
    suite = load(STATE / "final-suite.json", {})
    by_sha = {r["sha"]: r for r in ledger}
    factory = [r for r in ledger if r["author"].startswith("factory")]
    passes = [p for l in data["lanes"] for p in l["passes"] if p["outcome"] not in ("running", "testing")]
    outcomes = collections.Counter(p["outcome"] for p in passes)
    verdicts = collections.Counter(r["verdict"] for r in factory)
    disp = collections.Counter(r["disposition"].split(":")[0].split(" by")[0].split(" (")[0] for r in factory)
    on_branch = disp["included"] + disp["included after fix"]

    def share(rows):
        c = collections.Counter(r["verdict"] for r in rows)
        n = sum(c.values()) or 1
        return {k: c[k] for k in ("keep", "fix", "drop")} | {"n": sum(c.values()), "keep_pct": round(100 * c["keep"] / n)}

    per_lane = []
    for lane in data["lanes"]:
        c = collections.Counter(r["verdict"] for r in factory if r["lane"] == lane["lane"])
        per_lane.append({"lane": lane["lane"], "area": lane["focus"].split(":", 1)[-1].split("(")[0].strip(),
                         "keep": c["keep"], "fix": c["fix"], "drop": c["drop"]})
    written = [(c["added"], c["removed"]) for l in data["lanes"] for c in l["commits"]
               if by_sha[c["sha"]]["author"].startswith("factory")]
    integrated = [labels[r["sha"]] for r in factory if r["disposition"].startswith("included") and r["sha"] in labels]
    real = [v for v in integrated if v["real_bug"] and v["class"] != "minor"]
    hours = collections.Counter()
    for lane in data["lanes"]:
        for c in lane["commits"]:
            if by_sha[c["sha"]]["author"].startswith("factory"):
                hours[c["at"][11:13]] += 1
    prices = costs["prices_usd_per_million"]
    phases = [p | {"usd": money(p, prices)} for p in costs["phases"]]
    started = min(p["begin_at"] for p in passes if p["begin_at"])
    ended = max((p["end_at"] or p["begin_at"]) for p in passes if p["begin_at"])
    span = (datetime.fromisoformat(ended.replace("Z", "+00:00")) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() / 3600
    spot_overlap = [(s, labels[s["sha"]]) for s in spot["results"] if s["sha"] in labels]
    return {
        "started": started, "span_h": span, "lanes_n": len(data["lanes"]) - 1,
        "passes": len(passes), "outcomes": dict(outcomes),
        "factory_commits": len(factory), "verdicts": dict(verdicts), "disposition": dict(disp), "on_branch": on_branch,
        "lane0": share([r for r in factory if r["lane"] == "lane-0"]),
        "lanes": share([r for r in factory if r["lane"] != "lane-0"]),
        "per_lane": per_lane, "hours": hours,
        "lines_written": (sum(a for a, _ in written), sum(b for _, b in written)),
        "branch_lines": load(HERE / "branch-lines.json", {}),
        "tokens_total": sum(p["input"] + p["cached"] + p["output"] + p["reasoning"] + p["cache_write"] for p in phases),
        "phases": phases, "usd": sum(p["usd"] for p in phases),
        "classes": collections.Counter(v["class"] for v in integrated if v["real_bug"] or v["class"] == "meaningful-test"),
        "severity_real": collections.Counter(v["severity"] for v in real),
        "real": len(real), "real_hours": sum(v["eng_hours"] for v in real),
        "not_real": sum(1 for v in integrated if not v["real_bug"]),
        "not_real_tests": sum(1 for v in integrated if not v["real_bug"] and v["class"] == "meaningful-test"),
        "integrated_labelled": len(integrated),
        "calibration": (len(spot_overlap), sum(s["value"] == l["class"] for s, l in spot_overlap),
                        sum(s["real_bug"] == l["real_bug"] for s, l in spot_overlap)),
        "spot": spot["summary"], "suite": suite, "status": status,
    }


# ---------- charts ----------

def svg(width, height, body, label):
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}" '
            f'xmlns="http://www.w3.org/2000/svg" font-size="13">{body}</svg>')


def hbars(rows, label, left=300, scale_to=None):
    w, bar_h, gap = 760, 28, 10
    top = scale_to or max(r[1] for r in rows) or 1
    body = ""
    for i, (name, value, colour, note) in enumerate(rows):
        y = i * (bar_h + gap)
        length = (w - left - 120) * value / top
        body += (f'<text x="{left - 10}" y="{y + 19}" text-anchor="end" fill="{INK}">{esc(name)}</text>'
                 f'<rect x="{left}" y="{y}" width="{length:.1f}" height="{bar_h}" rx="3" fill="{colour}"/>'
                 f'<text x="{left + length + 8:.1f}" y="{y + 19}" fill="{INK}" font-weight="600">{esc(value)}'
                 f'<tspan fill="{MUTED}" font-weight="400"> {esc(note)}</tspan></text>')
    return svg(w, len(rows) * (bar_h + gap), body, label)


def funnel(n):
    rows = [("Agent passes", n["passes"], OTHER, ""),
            ("Commits written", n["factory_commits"], INK, ""),
            ("Kept or fixed by AI review", n["verdicts"].get("keep", 0) + n["verdicts"].get("fix", 0), FIX, ""),
            ("Integrated, full suite green", n["on_branch"], KEEP, f"after {n['disposition'].get('removed at human sign-off', 0)} removed at human sign-off"),
            ("Real bugs fixed (not minor)", n["real"], KEEP, f"≈{round(n['real_hours'])} engineer-hours")]
    body = ''
    names = ['Agent passes', 'Commits written', 'Passed AI review', 'Integrated', 'Real fixes']
    for i, ((_, value, colour, _), name) in enumerate(zip(rows, names)):
        x = 8 + i * 151
        body += (f'<rect x="{x}" y="18" width="138" height="108" rx="10" fill="{colour}" fill-opacity=".07"/>'
                 f'<text x="{x + 12}" y="40" fill="{MUTED}" font-size="11">0{i + 1} / {esc(name)}</text>'
                 f'<text x="{x + 12}" y="82" fill="{colour}" font-size="34" font-weight="600">{value}</text>'
                 f'<line x1="{x + 12}" y1="108" x2="{x + 126}" y2="108" stroke="{colour}" stroke-width="3"/>')
        if i < 4:
            body += f'<text x="{x + 139}" y="83" fill="{MUTED}">›</text>'
    body += (f'<text x="10" y="153" fill="{MUTED}" font-size="12">Activity → review → integration → estimated value</text>'
             f'<text x="750" y="153" text-anchor="end" fill="{KEEP}" font-size="12">≈{round(n["real_hours"])} engineer-hours estimated</text>')
    return svg(760, 170, body, "From agent activity to estimated value; stages count different units")


def value_chart(n):
    order = ["security-or-safety", "user-facing-bug", "reliability", "meaningful-test", "minor"]
    rows = [(CLASS_NAMES[c], n["classes"].get(c, 0), CLASS_COLOURS[c], "") for c in order]
    rows.append(("Not a real bug", n["not_real"] - n["not_real_tests"], DROP, "no real input triggers it"))
    body = '<text x="225" y="14" fill="' + MUTED + '" font-size="11">ONE TILE = ONE COMMIT · AI-CLASSIFIED</text>'
    for i, (name, value, colour, _) in enumerate(rows):
        y = 34 + i * 32
        body += f'<text x="210" y="{y + 12}" text-anchor="end" fill="{INK}">{esc(name)}</text>'
        for j in range(value):
            body += f'<rect x="{225 + j * 13}" y="{y}" width="10" height="15" rx="2" fill="{colour}"/>'
        body += f'<text x="{233 + value * 13}" y="{y + 12}" fill="{INK}" font-weight="600">{value}</text>'
    body += f'<text x="225" y="244" fill="{MUTED}" font-size="11">Value estimates are model judgments, calibrated on a small blind sample.</text>'
    return svg(760, 258, body, f"Classification of {n['integrated_labelled']} integrated commits; one tile per commit")


def stacked(rows, label, percent=False):
    if percent:
        body = ''
        for tick in range(0, 101, 25):
            x = 220 + tick * 4.8
            body += (f'<line x1="{x}" y1="32" x2="{x}" y2="111" stroke="{GRID}"/>'
                     f'<text x="{x}" y="132" text-anchor="middle" fill="{MUTED}" font-size="11">{tick}%</text>')
        for i, (name, r) in enumerate(rows):
            total = sum(r[k] for k in ('keep', 'fix', 'drop'))
            pct = 100 * r['keep'] / max(total, 1)
            y = 48 + i * 48
            colour = OTHER if i == 0 else KEEP
            body += (f'<text x="202" y="{y + 4}" text-anchor="end" fill="{INK}">{esc(name)}</text>'
                     f'<line x1="220" y1="{y}" x2="{220 + pct * 4.8}" y2="{y}" stroke="{colour}" stroke-width="3"/>'
                     f'<circle cx="{220 + pct * 4.8}" cy="{y}" r="7" fill="{colour}"/>'
                     f'<text x="{234 + pct * 4.8}" y="{y + 4}" fill="{colour}" font-weight="600">{round(pct)}%</text>'
                     f'<text x="202" y="{y + 19}" text-anchor="end" fill="{MUTED}" font-size="10">{r["keep"]} kept / {total} reviewed</text>')
        body += f'<text x="220" y="159" fill="{MUTED}" font-size="11">Keep rate · observational comparison, not a controlled experiment</text>'
        return svg(760, 176, body, 'AI-review keep rate: broad loop versus scoped lanes; sample sizes shown')
    w, bar_h, gap, left = 760, 24, 8, 250
    top = max(sum(r[k] for k in ("keep", "fix", "drop")) for _, r in rows) or 1
    body = ""
    for i, (name, r) in enumerate(rows):
        y = i * (bar_h + gap)
        total = sum(r[k] for k in ("keep", "fix", "drop")) or 1
        scale = (w - left - 70) / (total if percent else top)
        x = left
        body += f'<text x="{left - 10}" y="{y + 16}" text-anchor="end" fill="{INK}">{esc(name)}</text>'
        for key, colour in (("keep", KEEP), ("fix", FIX), ("drop", DROP)):
            if r[key]:
                width = r[key] * scale
                body += f'<rect x="{x:.1f}" y="{y}" width="{width:.1f}" height="{bar_h}" fill="{colour}"/>'
                if width > 22:
                    text = f"{round(100 * r[key] / total)}%" if percent else r[key]
                    body += f'<text x="{x + width / 2:.1f}" y="{y + 16}" text-anchor="middle" fill="#fff" font-size="12">{text}</text>'
                x += width
        if not percent:
            body += f'<text x="{x + 6:.1f}" y="{y + 16}" fill="{MUTED}" font-size="12">{total}</text>'
    ly = len(rows) * (bar_h + gap) + 8
    for j, (key, colour) in enumerate((("keep", KEEP), ("needs fix", FIX), ("drop", DROP))):
        body += (f'<rect x="{left + j * 110}" y="{ly}" width="12" height="12" fill="{colour}"/>'
                 f'<text x="{left + j * 110 + 18}" y="{ly + 11}" fill="{MUTED}" font-size="12">{key}</text>')
    return svg(w, ly + 20, body, label)


def timeline(n):
    # Use exact commit timestamps rather than smoothing six hourly bins.
    data, ledger = load('data.json'), load('ledger.json')
    factory = {r['sha'] for r in ledger if r['author'].startswith('factory')}
    times = sorted(datetime.fromisoformat(c['at'].replace('Z', '+00:00')).timestamp()
                   for lane in data['lanes'] for c in lane['commits'] if c['sha'] in factory)
    start = datetime.fromisoformat(n['started'].replace('Z', '+00:00')).timestamp()
    end = max(times[-1], start + n['span_h'] * 3600)
    x = lambda t: 55 + (t - start) / (end - start) * 660
    y = lambda v: 201 - v / len(times) * 160
    body = f'<text x="55" y="16" fill="{MUTED}" font-size="11">CUMULATIVE COMMITS · EXACT EVENT TIMES · UTC</text>'
    for v in (0, 40, 80, 120):
        body += (f'<line x1="55" y1="{y(v)}" x2="715" y2="{y(v)}" stroke="{GRID}"/>'
                 f'<text x="44" y="{y(v) + 4}" text-anchor="end" fill="{MUTED}" font-size="11">{v}</text>')
    path = f'M 55 201'
    for i, t in enumerate(times, 1):
        path += f' H {x(t):.2f} V {y(i):.2f}'
    body += (f'<path d="{path} L {x(times[-1])} 201 Z" fill="{KEEP}" fill-opacity=".08"/>'
             f'<path d="{path}" fill="none" stroke="{KEEP}" stroke-width="2.5"/>'
             f'<text x="{x(times[-1])}" y="{y(len(times)) - 10}" text-anchor="end" fill="{KEEP}" font-weight="600">{len(times)} commits</text>')
    for t in range(int(start // 3600 + 1) * 3600, int(end) + 1, 3600):
        hour = datetime.fromtimestamp(t, timezone.utc).strftime('%H:%M')
        body += f'<text x="{x(t)}" y="225" text-anchor="middle" fill="{MUTED}" font-size="11">{hour}</text>'
    body += f'<text x="55" y="249" fill="{MUTED}" font-size="11">Each step is a commit, not a validated fix. Review outcomes appear separately.</text>'
    return svg(760, 264, body, 'Cumulative factory commits over time; unsmoothed event-level data')


def hourly_timeline(n):
    hours = ["19", "20", "21", "22", "23", "00"]
    notes = {"19": "1 loop", "20": "1 loop", "21": "1 loop", "22": "4→14 lanes", "23": "14 lanes", "00": "credits out"}
    w, h, left, bottom = 760, 230, 20, 40
    top = max(n["hours"].get(x, 0) for x in hours) or 1
    bw = (w - left - 20) / len(hours)
    body = f'<line x1="{left}" y1="{h - bottom}" x2="{w - 10}" y2="{h - bottom}" stroke="{GRID}"/>'
    for i, hour in enumerate(hours):
        value = n["hours"].get(hour, 0)
        bh = (h - bottom - 40) * value / top
        cx = left + i * bw + bw / 2
        body += (f'<rect x="{cx - (bw - 16) / 2:.1f}" y="{h - bottom - bh:.1f}" width="{bw - 16:.1f}" height="{bh:.1f}" rx="3" fill="{INK}"/>'
                 f'<text x="{cx:.1f}" y="{h - bottom - bh - 6:.1f}" text-anchor="middle" fill="{INK}" font-weight="600">{value}</text>'
                 f'<text x="{cx:.1f}" y="{h - 16}" text-anchor="middle" fill="{INK}" font-size="12">{hour}:00</text>'
                 f'<text x="{cx:.1f}" y="{h - 2}" text-anchor="middle" fill="{MUTED}" font-size="11">{notes[hour]}</text>')
    return svg(w, h, body, "Commits per hour (UTC)")


# ---------- page ----------

def fmt_m(x):
    return f"{x / 1e6:,.0f}M" if x >= 1e7 else f"{x / 1e6:.1f}M"


def short(text, limit=20):
    out = ""
    for word in text.split():
        if len(out) + len(word) + 1 > limit:
            break
        out = f"{out} {word}".strip()
    while out.split() and out.split()[-1] in ("and", "or", "the", "of"):
        out = " ".join(out.split()[:-1])
    return out or text[:limit]


def page_parts(n):
    st = n["status"]
    signoff = st.get("signoff", "pending")
    production = st.get("production", "not yet deployed")
    bl = n["branch_lines"]
    usd = n["usd"]
    per_fix = usd / max(n["real"], 1)
    sev = n["severity_real"]
    lane0, lanes = n["lane0"], n["lanes"]
    cal_n, cal_class, cal_real = n["calibration"]
    writing = n["phases"][0]
    stats = [
        (f"{n['tokens_total'] / 1e6:,.0f}M", f"tokens ({round(100 * sum(p['cached'] for p in n['phases']) / n['tokens_total'])}% cached reads)"),
        (f"≈${usd:,.0f}", "at list prices, all-in"),
        (n["factory_commits"], "commits written"),
        (f"+{bl.get('prod_added', 0):,} / −{bl.get('prod_removed', 0):,}", "production lines, integrated"),
        (n["on_branch"], "commits integrated, tests green"),
        (n["real"], "real bugs fixed (not minor)"),
        (f"≈{round(n['real_hours'])} h", "senior-engineer time, est."),
        (f"{n['lanes_n']} · {n['span_h']:.1f} h", "parallel agents · wall clock"),
    ]
    tldr = f"""
<ul class=tldr>
<li><b>It made real value.</b> {n['real']} genuine bug fixes ({sev.get('high', 0)} high, {sev.get('medium', 0)} medium, {sev.get('low', 0)} low severity) survived review and the full test suite —
about {round(n['real_hours'])} hours of senior-engineer work by our estimate — for ≈${usd:,.0f} of tokens (≈${per_fix:,.0f} per real fix).</li>
<li><b>Focus was the lever.</b> One broad agent loop had {lane0['keep_pct']}% of its commits kept. Fourteen narrowly scoped agents: {lanes['keep_pct']}%.</li>
<li><b>Review was the real work.</b> {n['verdicts'].get('fix', 0)} commits needed fixing before they could merge; several would have shipped new bugs (one would have started logging passwords). About 1 in {round((n['integrated_labelled'] - n['classes'].get('meaningful-test', 0)) / max(n['not_real'] - n['not_real_tests'], 1))} claimed fixes weren't real bugs.</li>
</ul>"""
    sections = [
        ("What we did", f"""
<p>We pointed {n['lanes_n']} Grok 4.7 coding agents at our production codebase — Twitch AI agents, a streaming studio, overlays, auth, games, a public API — each confined
to one area, each on its own branch, looping <i>find one real bug → fix it with a test → commit</i> until the credits ran out. Instructions and policy prohibited deployment, secret access,
and editing their own rules. Then Claude Opus 5.5 reviewed every commit, we corrected accepted changes, merged the approved work onto current master, and ran the full test suite.</p>""",
         funnel(n)),
        ("Focus changed everything", f"""
<p>The first run was one agent with a broad prompt. It drifted into busywork: "tests" that read source files as text, plus helper code only those tests used.
Splitting the work into narrow lanes with a stricter gate moved the keep rate from {lane0['keep_pct']}% to {lanes['keep_pct']}%.</p>""",
         stacked([("One broad loop", lane0), (f"{n['lanes_n']} focused lanes", lanes)], "AI review verdicts by setup", percent=True)),
        ("What the value is", f"""
<p>Every integrated commit was classified (security, user-facing bug, reliability, test, minor), with a rough estimate of how long a senior engineer would need to find and fix it.
Against a blind spot-check by separate AI reviewers the classifier agreed on {cal_class} of {cal_n} classes and {cal_real} of {cal_n} "is this a real bug" calls, leaning slightly generous.
The biggest wins were in the overlay manager and stream services: refunded redeems that still played, a chat listener that died on one bad message, speech silently skipped.
Security-relevant fixes are counted here but described only by category until they are live.</p>""",
         value_chart(n)),
        ("Where it went wrong", f"""
<ul>
<li><b>Overclaiming.</b> {n['not_real'] - n['not_real_tests']} of the {n['integrated_labelled'] - n['classes'].get('meaningful-test', 0)} integrated fixes address something no real input can trigger, and {n['classes'].get('minor', 0)} more are real but minor. Commit messages read like every change fixed a bug.</li>
<li><b>Fixes that break things.</b> The reviewer caught {n['verdicts'].get('fix', 0)}, several by running a repro: a 5-second full-table scan on every tip, a chat clipper that lost 100 of 101 messages, filter fixes that opened new bypasses.</li>
<li><b>Duplicate work.</b> A lane restarted fresh and re-fixed bugs the earlier run had already fixed (3 commits superseded).</li>
<li><b>Shared state.</b> Parallel fix agents used git's stash, which all worktrees share, and swapped each other's work. Nothing was lost, but forbid it.</li>
</ul>""", None),
        ("Throughput", f"""
<p>{n['passes']} passes; a commit-landing pass took about 8 minutes. {fmt_m(writing['input'] + writing['output'] + writing['reasoning'])} fresh and {fmt_m(writing['cached'])} cached tokens for writing
(≈${n['phases'][0]['usd']:,.0f}), ≈${n['phases'][1]['usd']:,.0f} for the AI review, ≈${n['phases'][2]['usd']:,.0f} for supervision, integration and this write-up.
The agents wrote {n['lines_written'][0]:,} lines; {bl.get('prod_added', 0) + bl.get('test_added', 0):,} survived into the integration branch, two-thirds of them tests.</p>""", timeline(n)),
        ("Method, briefly", """
<ul>
<li>Each lane: own git worktree and branch, a one-paragraph focus, one commit per pass, at most 300 production lines.</li>
<li>External prompt, command-pattern policy and gate restrict deployment, service control, network commands, secrets and agent-behaviour files. These are workflow controls, not an OS sandbox.</li>
<li>Per-pass gate: compile, forbidden paths, all Node tests and related Python tests, counting only failures that are new versus the base commit.</li>
<li>Every 45 minutes: all lanes merged onto current master, full suite run, new failures bisected back to the lane that caused them.</li>
<li>After the run: AI review of every commit, blind spot-check of a random sample, fixes, hand-resolved conflicts, full suite on the result. Criteria for "value" were written before the results were read.</li>
</ul>""", None),
        ("Caveats", f"""
<ul>
<li>One night, one codebase, one writer model, no human control group. Value labels and hour estimates are AI judgements, calibrated on a small sample.</li>
<li>The command-pattern policy does not prevent arbitrary code from accessing the host or network. Scoped-lane results are observational, not a randomized comparison.</li>
<li>About {n['suite'].get('master_failing', 190)} tests already fail without production secrets, so some areas were barely tested by the gate.</li>
<li>Security findings are described by category only until fixed in production.</li>
<li>Status: human sign-off <b>{esc(signoff)}</b>; production <b>{esc(production)}</b>. Value counts once it is live and not reverted.</li>
</ul>""", None),
    ]
    by_lane = ("Verdicts per lane", "<p>Lane 0 is the first, broad run.</p>",
               stacked([(f"{r['lane'].replace('lane-', 'Lane ')} · {short(r['area'])}", r) for r in n["per_lane"]], "AI review verdicts per lane"))
    sections.insert(5, by_lane)
    deployment = load('deployment.json', {})
    if deployment:
        checks = ''.join(f'<li>{esc(check)}</li>' for check in deployment['checks'])
        limits = ''.join(f'<li>{esc(limit)}</li>' for limit in deployment['limitations'])
        sections.append(('Production release and validation', f'''
<p><b>The approved release is deployed.</b> Production commit <code>{esc(deployment['commit'])}</code>;
Studio edge release <code>{esc(deployment['edge_release'])}</code>, 27 October 2026.
All {deployment['edge_managed_artifacts_matching']} managed edge artifacts match the release.
The landing-page asset was hash-verified separately. All {deployment['lab_supervised_services_restarted']} supervised lab services,
plus clipper web and worker, restarted with new process IDs.</p>
<p><b>Validation:</b> the final signed-off Python comparison found {n['suite']['master_failing']} baseline failures
and {n['suite']['branch_failing']} candidate failures, with zero new failures. All 1,123 Node tests passed;
18 focused tests passed after merging two concurrent master commits; the ship gate also passed 28 browser smoke tests.
This is regression-relative validation, not a claim that every test passed.</p>
<p><b>Live checks completed:</b></p><ul>{checks}</ul>
<p><b>Initial sign-off exclusions:</b> the tips identity change and its partial performance fix;
the malformed-duration guard; and the screamhole audio refactor. The headline totals describe that initial release.</p>
<p><b>Post-review remediation:</b> seven content-filter findings originally delivered as expected-failure tests
were fixed in <code>9b2e14b0</code> (378 related tests passed). The tips recipient defect was subsequently
approved, fixed with an indexed lookup and ambiguous-identity rejection, and deployed in <code>2b77f12b</code>
(351 database/tips tests and 21 migration tests passed). The production index built in 36.2 seconds;
three read-only lookup samples took 14–20 ms. The lab stack was restarted after deployment.
These follow-up fixes are separate from the overnight accounting snapshot and initial-release value estimates.</p>
<p><b>Startup observation:</b> {esc(deployment['startup_observation'])}.</p>
<p><b>Verification limits:</b></p><ul>{limits}</ul>
<p>The analysis remains preliminary: value and engineer-hours are AI judgments, and token/cost totals reflect
the collected accounting snapshot rather than a continuously updated bill for subsequent report edits and rollout work.</p>
''', None))
    conclusion = (HERE / "conclusion.md").read_text() if (HERE / "conclusion.md").is_file() else None
    if conclusion:
        sections.append(("Conclusion", conclusion, None))
    return stats, tldr, sections


# Abuse- or exploit-relevant even when the classifier did not call them security.
WITHHELD = {"0a45f1b4", "f9b69fb6", "ec41b773", "2cc86180"}


def addendum(n):
    """Every commit the run produced, with what happened to it."""
    data, ledger = load("data.json"), {r["sha"]: r for r in load("ledger.json")}
    labels = {r["sha"]: r for r in load("value-labels.json", [])}
    live = bool(load("deployment.json", {}).get("commit"))
    rows, md = [], ["| Lane | Commit | Change | AI review | Outcome | Value |", "|---|---|---|---|---|---|"]
    for lane in data["lanes"]:
        for c in lane["commits"]:
            r, v = ledger[c["sha"]], labels.get(c["sha"], {})
            subject = c["subject"]
            sensitive = (v.get("class") == "security-or-safety" or c["sha"] in WITHHELD
                         or c["risk"] == "high" and subject.startswith(("auth", "ssrf", "db:")))
            if sensitive and (not live or not r['disposition'].startswith('included')):
                subject = "Security/safety-related change (category only; original patch excluded)"
            outcome = r["disposition"].split(":")[0].split(" by")[0].replace(" (factory tooling)", "")
            if lane["lane"] == "lane-14":
                outcome += " · findings fixed and deployed in follow-up"
            if c["sha"] in {"06365d18", "d4ffc92d"}:
                subject = "Tips recipient identity and lookup performance"
                outcome = "initially held · indexed replacement deployed"
            if not r["author"].startswith("factory"):
                outcome += " · not factory output"
            value = f"{CLASS_NAMES.get(v['class'], v['class'])}, {v['severity']}" + ("" if v["real_bug"] else ", not a real bug") if v else "—"
            cells = [lane["lane"].replace("lane-", ""), c["sha"], subject, c["verdict"] or "—", outcome, value]
            rows.append("<tr>" + "".join(f"<td>{esc(x)}</td>" for x in cells) + "</tr>")
            md.append("| " + " | ".join(str(x).replace("|", "/") for x in cells) + " |")
    table = ("<div class=tablewrap><table><thead><tr><th>Lane</th><th>Commit</th><th>Change</th><th>AI review</th><th>Outcome</th><th>Value</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table></div>")
    intro = (f"<p>All {len(rows)} commits on the lane branches, oldest first per lane. \"Not factory output\" marks commits made before the loop started or by hand; "
              "they are listed for completeness and excluded from every number above. Value labels are AI estimates (see \"What the value is\"). "
              "Excluded patches are not automatically unresolved defects: some were refactors, duplicates, or unsupported bug claims. "
              "Follow-up outcomes are identified separately; the original review verdicts remain unchanged.</p>")
    return intro + table, "\n".join(md)


CSS = """
:root{--ink:#1d2330;--muted:#5d6472;--rule:#e6e8ec;--accent:#3f8f5f}
*{box-sizing:border-box}body{margin:0;background:#fbfbf9;color:var(--ink);font:17px/1.6 Georgia,'Iowan Old Style',serif}
main{max-width:820px;margin:0 auto;padding:48px 24px 80px}
.kicker{font:600 13px/1 system-ui,sans-serif;letter-spacing:.12em;text-transform:uppercase;color:var(--accent);margin:0 0 14px}
h1{font-size:38px;line-height:1.15;margin:0 0 10px}.by{color:var(--muted);font:15px system-ui,sans-serif;margin:0 0 24px}
h2{font-size:23px;margin:40px 0 8px;padding-top:16px;border-top:1px solid var(--rule)}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:20px 0 24px}
.stat{background:#fff;border:1px solid var(--rule);border-radius:8px;padding:12px 14px}
.stat b{display:block;font:700 22px/1.25 system-ui,sans-serif}.stat span{font:12.5px/1.3 system-ui,sans-serif;color:var(--muted)}
.prelim{font:13px system-ui,sans-serif;background:#fff6e0;border:1px solid #ecd9a8;border-radius:6px;padding:8px 12px;margin:0 0 16px}@media print{figure,.stat,.tldr,tr{break-inside:avoid}th{position:static}}.tldr{background:#fff;border:1px solid var(--rule);border-left:4px solid var(--accent);border-radius:8px;padding:16px 20px 16px 38px;margin:0 0 8px}
figure{margin:18px 0 6px;padding:16px;background:#fff;border:1px solid var(--rule);border-radius:8px}
figure svg{width:100%;height:auto;font-family:system-ui,sans-serif}figcaption{font:13px system-ui,sans-serif;color:var(--muted);margin-top:6px}
.tablewrap{overflow-x:auto;margin:12px 0}table{border-collapse:collapse;font:12.5px/1.4 system-ui,sans-serif;width:100%}th,td{border-bottom:1px solid var(--rule);padding:6px 8px;text-align:left;vertical-align:top}th{background:#f2f3f5;position:sticky;top:0}td:nth-child(2){font-family:ui-monospace,monospace}ul{padding-left:22px}li{margin:6px 0}footer{margin-top:48px;color:var(--muted);font:13px system-ui,sans-serif}
@media(max-width:640px){.stats{grid-template-columns:repeat(2,1fr)}h1{font-size:28px}}
"""

TITLE = "Software Factory Technical Report"


def to_md(body):
    text = body.strip()
    for a, b in ((r"</?p>", "\n"), (r"<li>", "- "), (r"</?(ul|li)[^>]*>", ""), (r"</?b>", "**"), (r"</?i>", "*"), (r"<[^>]+>", "")):
        text = re.sub(a, b, text)
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(text)).strip()


def build():
    n = numbers()
    stats, tldr, sections = page_parts(n)
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("figure-*.svg"):
        old.unlink()
    parts = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
             f"<title>{esc(TITLE)}</title><meta name=description content='{esc(n['real'])} real bug fixes for ≈${n['usd']:,.0f} of tokens. What survived review, and what did not.'>"
             f"<style>{CSS}</style></head><body><main><p class=kicker>Field report · {n['started'][:10]}</p>"
              f"<h1>{esc(TITLE)}</h1><p>Software development with an unattended agent loop.</p><p class=by>Infected Research / FrackLabs</p>"
             + (f"<p class=prelim>Preliminary: {esc(n['status'].get('production', ''))}. Value and engineer-hours are AI estimates, not measured engineering effort.</p>" if n["status"].get("production") != "live" else ""),
             "<div class=stats>" + "".join(f"<div class=stat><b>{esc(v)}</b><span>{esc(k)}</span></div>" for v, k in stats) + "</div>", tldr]
    md = [f"# {TITLE}", "", "Software development with an unattended agent loop.", "", f"*Field report, {n['started'][:10]}. Infected Research / FrackLabs*", "",
          "| | |", "|---|---|"] + [f"| **{v}** | {k} |" for v, k in stats] + ["", to_md(tldr), ""]
    fig = 0
    for heading, body, chart in sections:
        parts.append(f"<h2>{esc(heading)}</h2>{body}")
        md += [f"## {heading}", "", to_md(body), ""]
        if chart:
            fig += 1
            caption = chart.split('aria-label="', 1)[1].split('"', 1)[0]
            parts.append(f"<figure>{chart}<figcaption>Figure {fig}. {esc(caption)}.</figcaption></figure>")
            (OUT / f"figure-{fig}.svg").write_text(chart.replace("<svg ", '<svg style="background:#fff;font-family:system-ui,sans-serif" ', 1))
            md += [f"![Figure {fig}. {caption}](figure-{fig}.svg)", ""]
    add_html, add_md = addendum(n)
    parts.append(f"<h2>Addendum: every change</h2>{add_html}")
    md += ["## Addendum: every change", "", to_md(add_html.split("<div", 1)[0]), "", add_md, ""]
    parts.append("<footer>Prices: public list rates (xAI, Anthropic), October 2026; credits were prepaid. Data, pre-registered criteria, per-commit ledger, "
                 "spot-check and value labels are in the factory repository's research/ folder.</footer></main></body></html>")
    (OUT / "index.html").write_text("".join(parts))
    (OUT / "report.md").write_text("\n".join(md) + "\n")
    print(OUT / "index.html", f"{fig} figures; real fixes {n['real']}, ${n['usd']:.0f}, {n['tokens_total'] / 1e6:.0f}M tokens")


if __name__ == "__main__":
    build()
