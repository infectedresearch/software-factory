"""Build the public web pages for FL-TR-2026-03 in round 1's site style.

    python3 research/round2/web.py      # writes research/round2/site/<slug>/ and the index fragment

Uses build.py's numbers, text and figures. Round 1's report.css is copied from the
published site so both reports read the same.
"""
from __future__ import annotations

import json
import shutil
import urllib.request
from pathlib import Path

import build as B

SITE = B.HERE / "site" / B.SLUG
BASE = f"https://fracktron.com/research/{B.SLUG}/"
DESCRIPTION = ("A second overnight run of fourteen coding agents. Commits had to carry a proof test: real fixes rose "
               "from 77 to 105 and minor commits fell from 18% to 8%, at the same price per fix. Task cards made "
               "building cheaper but not the night.")
SHARED_HEAD = """<link rel="icon" href="/research/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/research/favicon.ico" sizes="16x16 32x32 48x48">
<link rel="apple-touch-icon" href="/research/apple-touch-icon.png" sizes="180x180">
<meta name="theme-color" content="#132c24">
<meta property="og:site_name" content="FrackLabs">
<meta property="og:locale" content="en_US">
<meta property="og:image" content="https://fracktron.com/research/social-card.png">
<meta property="og:image:type" content="image/png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="FrackLabs Research — papers, code, and experiment records from Infected Research.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:image" content="https://fracktron.com/research/social-card.png">
<meta name="twitter:image:alt" content="FrackLabs Research — papers, code, and experiment records.">"""
HEADER = ('<header><div class="header-inner"><a class="wordmark" href="/"><span>[</span> FRACKTRON <span>]</span></a>'
          '<nav aria-label="Site navigation"><a href="/">Home</a><a href="/research/" aria-current="page">Research</a>'
          '<a href="https://github.com/infectedresearch/software-factory">GitHub</a></nav>'
          '<span class="header-note">INFECTED RESEARCH / FRACKLABS</span></div></header>')
esc = B.esc


def meta(title: str, url: str) -> str:
    return (f'<link rel="canonical" href="{url}"><meta property="og:type" content="article">'
            f'<meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(DESCRIPTION)}">'
            f'<meta property="og:url" content="{url}"><meta name="twitter:title" content="{esc(title)}">'
            f'<meta name="twitter:description" content="{esc(DESCRIPTION)}">\n{SHARED_HEAD}')


def card_figure(n) -> str:
    """Index-card figure: round 1 against round 2, real fixes and minor share."""
    r1, r2 = n["r1"], n["r2"]
    w, h, x0, bw = 430, 300, 118, 280
    rows = [("Real fixes merged", r1["real"], r2["real"], 120, ""),
            ("Minor share", round(100 * r1["minor"] / r1["integrated"]), round(100 * r2["minor"] / r2["integrated"]), 25, "%"),
            ("Tokens per real fix", round(r1["per_real"], 2), round(r2["per_real"], 2), 6.5, "$")]
    body = []
    for i, (name, a, b, top, unit) in enumerate(rows):
        y = 26 + i * 92
        body.append(f'<text x="0" y="{y}" fill="#edf4ee" font-size="12" font-weight="600">{name}</text>')
        for j, (label, v, col) in enumerate((("Round 1", a, "#a5b4a2"), ("Round 2", b, "#58ff7d"))):
            yy = y + 12 + j * 30
            txt = f"${v:.2f}" if unit == "$" else f"{v}{unit}"
            body.append(f'<text x="{x0 - 8}" y="{yy + 15}" text-anchor="end" fill="#a5b4a2" font-size="11">{label}</text>'
                        f'<rect x="{x0}" y="{yy}" width="{v / top * bw:.1f}" height="22" fill="{col}"/>'
                        f'<text x="{x0 + v / top * bw + 6:.1f}" y="{yy + 15}" fill="#edf4ee" font-size="11.5" font-weight="600">{txt}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" '
            f'aria-label="Round 1 against round 2: real fixes merged, minor share, token cost per real fix" '
            f'font-family="monospace">{"".join(body)}</svg>')


def report_html(n, t) -> str:
    f, c, r1, r2 = n["arms"]["free"], n["arms"]["cards"], n["r1"], n["r2"]
    stats = [(str(r2["integrated"]), "commits merged onto master"), (str(r2["real"]), f"real bugs fixed (round 1: {r1['real']})"),
             (B.pct(r2["minor"] / r2["integrated"]), f"minor commits (round 1: {B.pct(r1['minor'] / r1['integrated'])})"),
             (f"${r2['per_real']:.2f}", f"tokens per real fix (round 1: ${r1['per_real']:.2f})"),
             (f"≈${r2['tokens_usd']:.0f}", "tokens at list price"), (f"{r2['gate_total'] - r2['gate_fail']} / {r2['gate_total']}", "commits passed the proof gate"),
             (B.pct(r2["keep_rate"]), "survived AI review"), (f"{100 * n['primary_diff']:+.0f}%", "card arm vs free, all-in")]
    figs = [(B.fig_timeline(n), "Figure 1. Commits that survived the gate and review, by arm, over time. Card lanes flatten when the queue empties; both stop when the quota runs out."),
            (B.fig_cost(n), "Figure 2. Spend per arm at list prices. Card building is cheap; scouting is most of the card arm's cost."),
            (B.fig_funnel(n), "Figure 3. The card pipeline. Approval passed every reviewed card; conflicts between card lanes were resolved by hand at merge."),
            (B.fig_classes(n), "Figure 4. Surviving commits by value class, labelled blind to arm.")]
    fig = lambda i: f"<figure>{figs[i][0]}<figcaption>{esc(figs[i][1])}</figcaption></figure>"  # noqa: E731
    title = f"{B.TITLE} / FrackLabs"
    parts = [
        f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{esc(B.TITLE)} — {B.DRAFT}</title><meta name=description content='{esc(DESCRIPTION)}'><link rel=\"stylesheet\" href=\"report.css\">"
        f"{meta(title, BASE + 'report.html')}</head><body><main>",
        f"<p><a href=\"./\">← Research overview</a></p><p class=kicker>{B.DRAFT} · 2026-11-10</p><h1>{esc(B.TITLE)}</h1>",
        f"<p>{esc(B.SUBTITLE)}</p><p class=by>Infected Research / FrackLabs · {B.REPORT_ID}</p>",
        "<p class=prelim>Draft: all changes merged onto master and deployed 2026-11-10; full suite showed no new failures. "
        "Value labels and engineer-hours are AI estimates, not measured effort.</p>",
        "<div class=stats>" + "".join(f"<div class=stat><b>{esc(v)}</b><span>{esc(k)}</span></div>" for v, k in stats) + "</div>",
        "<ul class=tldr>" + "".join(f"<li><b>{esc(h)}.</b> {esc(x)}</li>" for h, x in t["highlights"]) + "</ul>",
        f"<h2>Abstract</h2><p>{t['abstract']}</p>",
        f"<h2>Against round 1</h2><div class=tablewrap>{t['vs_round1'].split('</table>')[0]}</table></div>{t['vs_round1'].split('</table>')[1]}",
        "<h2>Findings</h2><ol>" + "".join(f"<li>{x}</li>" for x in t["findings"]) + "</ol>",
        f"<h2>What we did</h2>{t['method']}",
        f"<h2>How the night went</h2>{t['timeline']}{fig(0)}",
        f"<h2>Two arms: free and scout-directed</h2><div class=tablewrap>{t['results'].split('</table>')[0]}</table></div>{t['results'].split('</table>', 1)[1]}{fig(1)}{fig(2)}",
        f"<h2>What the commits were worth</h2>{t['value']}{fig(3)}",
        f"<h2>What we would change</h2>{t['lessons']}",
        f"<h2>Caveats</h2>{t['limits']}",
        f"<h2>Files</h2><ul><li><a href=\"{B.PDF_NAME}\">PDF</a> (the same report, typeset)</li><li><a href=\"data.json\">data.json</a>: sanitized per-commit records, per-arm numbers, round comparison, token accounting, spot-check</li>"
        "<li><a href=\"criteria.md\">criteria.md</a>: the pre-registration, written before the run</li>"
        "<li>Harness source for both rounds, with a script that recomputes these numbers: <a href=\"https://github.com/infectedresearch/software-factory\">GitHub</a> (v1.1.0). Round 1: <a href=\"/research/software-factory/\">FL-TR-2026-01</a>. Application source, diffs, commit bodies, cards and agent transcripts are not published. "
        "As in round 1, security and safety changes are listed by category only.</li></ul>",
        f"<h2>Addendum: every surviving commit</h2><div class=tablewrap>{B.appendix(n).replace(' class=changes', '')}</div>",
        "<footer>Infected Research / FrackLabs · paper and data CC BY 4.0 · <a href=\"/research/\">Research index</a></footer></main></body></html>",
    ]
    return "".join(parts).replace("<table class=kr>", "<table>")


def overview_html(n) -> str:
    r1, r2, f, c = n["r1"], n["r2"], n["arms"]["free"], n["arms"]["cards"]
    title = f"{B.TITLE} — {B.DRAFT} / FrackLabs"
    facts = [("Merged", f"{r2['integrated']} commits / {r2['real']} real bug fixes (round 1: {r1['integrated']} / {r1['real']})"),
             ("Noise", f"{B.pct(r2['minor'] / r2['integrated'])} minor (round 1: {B.pct(r1['minor'] / r1['integrated'])}); "
                       f"{r2['gate_total'] - r2['gate_fail']} of {r2['gate_total']} commits passed the proof gate"),
             ("Token accounting", f"≈${r2['tokens_usd']:.0f} at list prices / ${r2['per_real']:.2f} per real fix (round 1: ${r1['per_real']:.2f})"),
             ("Two arms", f"free ${f['primary']:.2f} vs scout-directed ${c['primary']:.2f} per non-minor merged commit; pre-registered threshold 30%")]
    return (
        f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{esc(title)}</title><meta name=\"description\" content=\"{esc(DESCRIPTION)}\"><link rel=\"stylesheet\" href=\"/research/research.css\">"
        f"<meta name=\"citation_title\" content=\"{esc(B.TITLE)}\"><meta name=\"citation_author\" content=\"Infected Research / FrackLabs\">"
        f"<meta name=\"citation_publication_date\" content=\"2026/11/10\"><meta name=\"citation_pdf_url\" content=\"{BASE}{B.PDF_NAME}\">"
        f"{meta(title, BASE)}</head><body><a class=\"skip\" href=\"#main\">Skip to report</a>{HEADER}\n"
        f"<main id=\"main\" class=\"paper\">\n<p class=\"record\"><time datetime=\"2026-11-10\">2026-11-10</time><span>{B.REPORT_ID} · draft</span></p>\n"
        f"<h1>{esc(B.TITLE)}</h1>\n<p class=\"subtitle\">{esc(B.SUBTITLE)}</p>\n<p class=\"byline\">Infected Research / FrackLabs · {B.DRAFT}</p>\n"
        f"<nav class=\"files\" aria-label=\"Publication files\"><a href=\"{B.PDF_NAME}\">PDF</a><a href=\"report.html\">HTML</a><a href=\"data.json\">Data</a><a href=\"criteria.md\">Pre-registration</a><a href=\"https://github.com/infectedresearch/software-factory\">GitHub</a></nav>\n"
        f"<p>A second overnight run of 14 coding agents on the same private production codebase as <a href=\"/research/software-factory/\">round 1</a>. "
        f"Every commit had to name a test that fails before the change and passes after, and an AI reviewer judged each commit as it landed. "
        f"Half the lanes hunted for their own work; the other half built task cards written by a read-only scout. Everything that survived was merged, tested and deployed.</p>\n"
        "<dl class=\"facts\">" + "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in facts) + "</dl>\n"
        "<p class=\"note\">One night, one codebase, compared before and after rather than against a control. The harness changes shipped together, so their effects cannot be separated. "
        "Value labels are model judgments, not measured savings.</p>\n"
        "<h2>Files</h2><p>The paper in PDF and HTML, sanitized per-commit records and the pre-registration. The harness, including round 2's card scout and proof gate, is on GitHub. Application source, diffs and agent transcripts are excluded.</p>\n"
        "<p class=\"note\">Code: MIT. Paper and data: CC BY 4.0.</p>\n"
        f"<h2>Citation</h2><p class=\"citation\">Infected Research / FrackLabs. 2026. <cite>{esc(B.TITLE)}: {esc(B.SUBTITLE)}</cite> {B.REPORT_ID}.</p>\n"
        "</main><footer><a href=\"/research/\">Research index</a><a href=\"/research/feed.xml\">RSS</a></footer></body></html>\n")


def index_entry(n) -> str:
    r1, r2 = n["r1"], n["r2"]
    return (
        '<section class="entry" aria-labelledby="syw-title">\n'
        f'<div class="entry-top"><span>{B.REPORT_ID}</span><time datetime="2026-11-10">10 NOV 2026</time><span class="preliminary">DRAFT RESEARCH REPORT 003</span></div>\n'
        '<div class="entry-grid"><div class="entry-copy"><p class="eyebrow">03 / SOFTWARE ENGINEERING</p>'
        f'<h2 id="syw-title"><a href="/research/{B.SLUG}/"><span>{esc(B.TITLE)}</span> <small>{B.DRAFT}</small></a></h2>'
        '<p class="subtitle">Round two of the software factory: proof-carrying commits.</p>'
        f'<p>Fourteen coding agents, a second night. Every commit had to prove itself with a test. Real fixes rose from {r1["real"]} to {r2["real"]}; '
        f'minor commits fell from {B.pct(r1["minor"] / r1["integrated"])} to {B.pct(r2["minor"] / r2["integrated"])}, at the same price per fix.</p>'
        '<p class="annotation">Free lanes against scout-written task cards. Pre-registered, merged and deployed.</p>'
        f'<nav class="files" aria-label="Show Your Work publication files"><a class="primary-file" href="/research/{B.SLUG}/{B.PDF_NAME}">READ THE DRAFT <span aria-hidden="true">↗</span></a>'
        f'<a href="/research/{B.SLUG}/report.html">HTML</a><a href="/research/{B.SLUG}/data.json">Data</a><a href="https://github.com/infectedresearch/software-factory">GitHub</a></nav></div>'
        f'<figure class="review-plot"><figcaption><span>RUN 02 / AGAINST RUN 01</span><span>MERGED COMMITS</span></figcaption>'
        f'<img src="/research/{B.SLUG}/round-comparison.svg" width="430" height="300" alt="Round 1 against round 2: real fixes merged {r1["real"]} to {r2["real"]}, minor share {B.pct(r1["minor"] / r1["integrated"])} to {B.pct(r2["minor"] / r2["integrated"])}, tokens per real fix ${r1["per_real"]:.2f} to ${r2["per_real"]:.2f}.">'
        '<p>AI-labelled · same classes in both rounds</p></figure></div>\n'
        f'<dl class="run-numbers"><div><dt>REAL FIXES MERGED</dt><dd>{r1["real"]} <span>→</span> {r2["real"]}</dd><p>round 1 → round 2</p></div>'
        f'<div><dt>MINOR SHARE</dt><dd>{B.pct(r1["minor"] / r1["integrated"])} <span>→</span> {B.pct(r2["minor"] / r2["integrated"])}</dd><p>of merged commits</p></div>'
        f'<div><dt>PER REAL FIX</dt><dd>${r2["per_real"]:.2f}</dd><p>tokens at list price; round 1 ${r1["per_real"]:.2f}</p></div></dl>'
        f'<div class="entry-bottom"><span>Paper / data / pre-registration</span><a href="/research/{B.SLUG}/">Report details →</a></div>\n'
        '</section>\n')


def data_json(n) -> dict:
    fields = ("short", "lane", "arm", "at", "verdict", "card")
    commits = [{**{k: r[k] for k in fields}, "subject": B.public_subject(r),
                "class": r["label"]["class"], "real_bug": r["label"]["real_bug"], "severity": r["label"]["severity"],
                "eng_hours": r["label"]["eng_hours"], "applied_without_conflict": r["applies"], "merged": r["merged"],
                "not_merged_reason": None if r["merged"] else n["merge"]["not_merged"][r["short"][:8]]}
               for r in sorted(n["rows"], key=lambda r: r["at"])]
    arms = {a: {k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items() if not isinstance(v, (list, dict))}
            for a, x in n["arms"].items()}
    spot = [{k: s.get(k) for k in ("sha", "arm", "fails_before", "passes_after", "class", "real_bug", "overclaims",
                                   "classifier_class", "classifier_real_bug")} for s in n["spot"]["results"]]
    return {"report": B.REPORT_ID, "title": B.TITLE, "license": "CC BY 4.0", "author": "Infected Research / FrackLabs",
            "notes": "Costs are list-price token accounting. Classes, real_bug, severity and eng_hours are AI labels made blind to arm. "
                     "As in round 1, security and safety changes are listed by category only.",
            "round_comparison": {"round1": n["r1"], "round2": n["r2"]}, "arms": arms, "primary_diff": round(n["primary_diff"], 4),
            "cards": n["cards"], "scout_usd": n["scout"], "spotcheck": {"method": n["spot"]["method"], "summary": n["spot"]["summary"], "results": spot},
            "commits": commits}


def main() -> None:
    n = B.numbers()
    t = B.text(n)
    pdf = B.pdf(B.paper(n, t))
    B.markdown(n, t)
    SITE.mkdir(parents=True, exist_ok=True)
    css = urllib.request.urlopen("https://fracktron.com/research/software-factory/report.css").read()
    (SITE / "report.css").write_bytes(css)
    (SITE / "report.html").write_text(report_html(n, t))
    (SITE / "index.html").write_text(overview_html(n))
    (SITE / "round-comparison.svg").write_text(card_figure(n))
    (SITE / "data.json").write_text(json.dumps(data_json(n), indent=1))
    shutil.copy(B.HERE / "criteria.md", SITE / "criteria.md")
    shutil.copy(pdf, SITE / B.PDF_NAME)
    (B.HERE / "site" / "index-entry.html").write_text(index_entry(n))
    print(SITE, sorted(p.name for p in SITE.iterdir()))


if __name__ == "__main__":
    main()
