"""Typeset the research write-up as a technical-report PDF (page/paper.html -> page/*.pdf).

Reuses build.py's numbers, text, figures and change list, with a print layout.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path

import build

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
PDF = build.OUT / "software-factory-preliminary.pdf"
REPORT_ID = "FL-TR-2026-01"
AUTHORS = "Infected Research / FrackLabs"

# A quieter palette for print.
build.KEEP, build.FIX, build.DROP, build.OTHER = "#0f766e", "#b7791f", "#b4413a", "#9aa1ab"
build.INK, build.MUTED, build.GRID = "#14181f", "#5b6270", "#e3e6ea"
build.CLASS_COLOURS = {"security-or-safety": "#1e4e8c", "user-facing-bug": "#0f766e", "reliability": "#4d7c0f",
                       "meaningful-test": "#6d4fa1", "minor": "#9aa1ab"}


def font_face(family: str, file: str, weight: int, style: str = "normal") -> str:
    data = base64.b64encode((FONTS / file).read_bytes()).decode()
    return (f"@font-face{{font-family:'{family}';src:url(data:font/woff2;base64,{data}) format('woff2');"
            f"font-weight:{weight};font-style:{style}}}")


FACES = "".join([
    font_face("Inter", "inter-400-normal.woff2", 400), font_face("Inter", "inter-500-normal.woff2", 500),
    font_face("Inter", "inter-600-normal.woff2", 600), font_face("Inter", "inter-700-normal.woff2", 700),
    font_face("Source Serif", "source-serif-4-400-normal.woff2", 400),
    font_face("Source Serif", "source-serif-4-400-italic.woff2", 400, "italic"),
    font_face("Source Serif", "source-serif-4-600-normal.woff2", 600),
    font_face("JetBrains Mono", "jetbrains-mono-400-normal.woff2", 400),
])

CSS = FACES + """
@page{size:Letter;margin:0.85in 0.9in 0.85in 0.9in}
:root{--ink:#14181f;--muted:#5b6270;--rule:#d9dde3;--soft:#f4f5f7;--accent:#0f766e}
*{box-sizing:border-box}
html{font:10.4pt/1.52 'Source Serif',Georgia,serif;color:var(--ink);-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{margin:0}
.sans,h1,h2,h3,.label,.meta,table,figcaption,.kr,.findings b{font-family:Inter,system-ui,sans-serif}
.label{font-size:7.6pt;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:var(--accent);margin:0 0 10pt}
h1{font-size:20pt;line-height:1.12;font-weight:700;letter-spacing:-.015em;margin:0 0 8pt}
.subtitle{font-size:11.5pt;line-height:1.35;color:var(--muted);margin:0 0 12pt;font-style:italic}
.meta{font-size:8.6pt;color:var(--muted);display:flex;gap:22pt;border-top:1.4pt solid var(--ink);border-bottom:.6pt solid var(--rule);padding:6pt 0;margin:0 0 12pt}
.meta b{color:var(--ink);font-weight:600;display:block;font-size:7pt;letter-spacing:.12em;text-transform:uppercase;margin-bottom:1pt}
.status{font-family:Inter,sans-serif;font-size:8pt;background:#fbf6ea;border-left:2.5pt solid #b7791f;padding:5pt 9pt;margin:0 0 12pt;color:#5a4a1f}
.abstract{margin:0 0 12pt;padding:0 0 0 0}
.abstract h3{font-size:8pt;letter-spacing:.14em;text-transform:uppercase;margin:0 0 5pt;color:var(--ink)}
.abstract p{margin:0;text-align:justify;hyphens:auto}
.kr{width:100%;border-collapse:collapse;margin:2pt 0 14pt;font-size:8.2pt;break-inside:avoid}
.kr td{padding:3pt 6pt;border-bottom:.6pt solid var(--rule);vertical-align:baseline}
.kr td.v{font-weight:600;font-size:11pt;white-space:nowrap;width:22%;font-variant-numeric:tabular-nums}
.kr td.k{color:var(--muted);width:28%}
.kr caption{text-align:left;font-size:7.6pt;font-weight:600;letter-spacing:.14em;text-transform:uppercase;padding:0 0 5pt;caption-side:top}
h2{font-size:12.2pt;font-weight:600;margin:20pt 0 6pt;letter-spacing:-.005em;break-after:avoid}
h2 .n{color:var(--accent);margin-right:7pt;font-variant-numeric:tabular-nums}
p{margin:0 0 7pt;text-align:justify;hyphens:auto}
ul,ol{margin:2pt 0 8pt;padding-left:15pt}li{margin:2.5pt 0}
.findings{counter-reset:f;list-style:none;padding:0;margin:0 0 6pt}
.findings li{counter-increment:f;position:relative;padding:0 0 0 22pt;margin:0 0 6pt}
.findings li::before{content:counter(f);position:absolute;left:0;top:.5pt;font:600 8pt Inter,sans-serif;color:#fff;background:var(--accent);width:13pt;height:13pt;border-radius:7pt;text-align:center;line-height:13pt}
figure{margin:10pt 0 12pt;break-inside:avoid}
figure svg{width:100%;height:auto;font-family:Inter,sans-serif}
figcaption{font-size:8.2pt;color:var(--muted);border-top:.6pt solid var(--rule);padding-top:4pt;margin-top:4pt}
figcaption b{color:var(--ink)}
.appendix{break-before:page}section.keep{break-inside:avoid}
.changes col.l{width:5%}.changes col.c{width:9%}.changes col.s{width:46%}.changes col.r{width:7%}.changes col.o{width:15%}.changes col.v{width:18%}
.changes{width:100%;border-collapse:collapse;font-size:6.9pt;line-height:1.3}
.changes th{text-align:left;font-weight:600;border-bottom:1pt solid var(--ink);padding:3pt 4pt;font-size:6.6pt;letter-spacing:.06em;text-transform:uppercase}
.changes td{border-bottom:.4pt solid var(--rule);padding:2.6pt 4pt;vertical-align:top}
.changes tr{break-inside:avoid}.changes td:nth-child(2){font-family:'JetBrains Mono',monospace;font-size:6.5pt}
.changes thead{display:table-header-group}
.refs{font-size:8.4pt}.refs li{margin:2pt 0}
code{font-family:'JetBrains Mono',monospace;font-size:8.4pt}
"""


def abstract(n: dict) -> str:
    sev = n["severity_real"]
    return (f"We report a one-night field experiment in unattended software production. {n['lanes_n']} parallel coding agents "
            f"(Grok 4.7), each confined to one subsystem of a live production codebase, ran for {n['span_h']:.1f} hours under "
            f"workflow restrictions and an automatic per-commit gate. They produced {n['factory_commits']} commits. Every commit "
            f"was then reviewed by a second model (Claude Opus 5.5), a random sample was re-judged blind, flagged defects were "
            f"fixed, all work was integrated onto current master, the full test suite was compared against master, and the "
            f"highest-risk changes were read and approved by a human. {n['on_branch']} commits survived with no new test "
            f"failures. AI classification estimates that {n['real']} fix real defects ({sev.get('high', 0)} high, {sev.get('medium', 0)} medium, "
            f"{sev.get('low', 0)} low severity), an estimated {round(n['real_hours'])} senior-engineer hours of work, for "
            f"≈${n['usd']:,.0f} in tokens at list prices. Narrow scoping raised the review keep rate from {n['lane0']['keep_pct']}% "
            f"to {n['lanes']['keep_pct']}%. Review, not generation, was the binding constraint: {n['verdicts'].get('fix', 0)} "
            f"commits required correction before merge, and about one claimed fix in seven addressed a defect no real input "
            f"could trigger.")


def key_results(n: dict) -> str:
    bl, ph = n["branch_lines"], n["phases"]
    rows = [
        ("Parallel agents · wall clock", f"{n['lanes_n']} · {n['span_h']:.1f} h", "Agent passes", f"{n['passes']}"),
        ("Commits written", f"{n['factory_commits']}", "Integrated, suite green", f"{n['on_branch']}"),
        ("Real defects fixed", f"{n['real']}", "Est. senior-engineer time", f"≈{round(n['real_hours'])} h"),
        ("Tokens processed", f"{n['tokens_total'] / 1e6:,.0f}M", "Cost at list prices", f"≈${n['usd']:,.0f}"),
        ("Write · review · supervise", f"${ph[0]['usd']:,.0f} · ${ph[1]['usd']:,.0f} · ${ph[2]['usd']:,.0f}", "Cost per real fix", f"≈${n['usd'] / max(n['real'], 1):,.2f}"),
        ("Production lines (+/−)", f"+{bl.get('prod_added', 0):,} / −{bl.get('prod_removed', 0):,}", "Test lines (+/−)", f"+{bl.get('test_added', 0):,} / −{bl.get('test_removed', 0):,}"),
        ("Keep rate, broad → scoped", f"{n['lane0']['keep_pct']}% → {n['lanes']['keep_pct']}%", "Blind spot-check agreement", f"{n['spot']['agree_keep']} / 12"),
    ]
    body = "".join(f"<tr><td class=k>{build.esc(a)}</td><td class=v>{build.esc(b)}</td><td class=k>{build.esc(c)}</td><td class=v>{build.esc(d)}</td></tr>"
                   for a, b, c, d in rows)
    return f"<table class=kr><caption>Key results</caption>{body}</table>"


def findings(tldr: str) -> str:
    return tldr.replace("<ul class=tldr>", "<ol class=findings>").replace("</ul>", "</ol>")


def paper() -> Path:
    n = build.numbers()
    _, tldr, sections = build.page_parts(n)
    add_html, _ = build.addendum(n)
    status = n["status"]
    prelim = status.get("production") != "live"
    out = [f"<!doctype html><html lang=en><head><meta charset=utf-8><title>{build.esc(build.TITLE)}</title><style>{CSS}</style></head><body>",
           f"<p class=label>Technical report · {REPORT_ID}</p>",
           f"<h1>{build.esc(build.TITLE)}</h1>",
           "<p class=subtitle>Software development with an unattended agent loop.</p>",
           f"<div class=meta><div><b>Authors</b>{AUTHORS}</div><div><b>Date</b>{n['started'][:10]}</div>"
           f"<div><b>Writer · reviewer</b>Grok 4.7 · Claude Opus 5.5</div><div><b>Status</b>{'Preliminary' if prelim else 'Final'}</div></div>"]
    if prelim:
        out.append(f"<p class=status><b>Preliminary.</b> Human sign-off: {build.esc(status.get('signoff', 'pending'))}. "
                   f"Production: {build.esc(status.get('production', 'not yet deployed'))}. Value and engineer-hours are AI estimates, not measured engineering effort.</p>")
    out += [f"<div class=abstract><h3>Abstract</h3><p>{abstract(n)}</p></div>", key_results(n),
            "<h2><span class=n>1</span>Summary of findings</h2>", findings(tldr)]
    fig = 0
    for i, (heading, body, chart) in enumerate(sections, start=2):
        block = f"<h2><span class=n>{i}</span>{build.esc(heading)}</h2>{body}"
        if chart:
            fig += 1
            caption = chart.split('aria-label="', 1)[1].split('"', 1)[0]
            block += f"<figure>{chart}<figcaption><b>Figure {fig}.</b> {build.esc(caption)}.</figcaption></figure>"
            block = f"<section class=keep>{block}</section>" if len(body) < 900 else block
        out.append(block)
    out.append("<h2><span class=n>{}</span>Data and reproducibility</h2>".format(len(sections) + 2))
    out.append("<ul class=refs><li>Pre-registered criteria for value and noise, written before review results were read: <code>research/criteria.md</code>.</li>"
               "<li>Per-commit ledger, blind spot-check sample (seed 20261027) and value labels: <code>research/ledger.json</code>, "
               "<code>spotcheck.json</code>, <code>value-labels.json</code>.</li>"
               "<li>Token counts from the agent harness session store; prices are public list rates (xAI, Anthropic), October 2026. Credits were prepaid.</li>"
               "<li>Full-suite comparisons: every Python test file and every Node test, branch versus master, new failures rerun three times.</li></ul>")
    out.append(f"<div class=appendix><h2><span class=n>A</span>Appendix: every change</h2>"
               + add_html.replace("<div class=tablewrap><table>", "<table class=changes><colgroup><col class=l><col class=c><col class=s><col class=r><col class=o><col class=v></colgroup>").replace("</table></div>", "</table>") + "</div>")
    out.append("</body></html>")
    html_path = build.OUT / "paper.html"
    html_path.write_text("".join(out))
    return html_path


PDF_JS = """
const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.goto('file://%(html)s', { waitUntil: 'load' });
  await p.evaluate(() => document.fonts.ready);
  const head = (t) => `<div style="font:7px Inter,Helvetica,sans-serif;width:100%%;padding:0 0.9in;color:#8a909a;display:flex;justify-content:space-between">${t}</div>`;
  await p.pdf({ path: '%(pdf)s', format: 'Letter', printBackground: true, preferCSSPageSize: true, displayHeaderFooter: true,
    headerTemplate: head('<span>FrackLabs · Technical report %(rid)s</span><span>%(state)s</span>'),
    footerTemplate: head('<span>Infected Research / FrackLabs</span><span><span class=pageNumber></span> / <span class=totalPages></span></span>') });
  await b.close();
})();
"""


def pdf(html_path: Path) -> None:
    status = build.load("status.json", {})
    script = build.OUT / "paper-pdf.cjs"
    script.write_text(PDF_JS % {"html": html_path, "pdf": PDF, "rid": REPORT_ID,
                                "state": "Preliminary" if status.get("production") != "live" else "Final"})
    modules = HERE.parent / "node_modules"
    subprocess.run(["node", str(script)], check=True, env={"PATH": "/usr/bin:/bin:/usr/local/bin", "NODE_PATH": str(modules),
                                                          "HOME": str(Path.home())})
    print(PDF)


if __name__ == "__main__":
    pdf(paper())
