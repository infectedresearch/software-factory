# Reproducibility and evidence boundaries

## Reproducible from this repository

- Headline arithmetic, original keep/fix/drop rates, lane timing curves and value-label totals.
- List-price cost calculations from the published aggregate token counters and recorded rates.
- The 147-row disposition ledger (132 factory commits; the rest are labeled non-factory work).
- Comparison of the 12 blind keep checks with original labels.
- Report HTML and PDF rendering, with category-only public metadata.
- Factory unit/integration tests on synthetic repositories, including baseline-relative failure detection.

## Not independently reproducible from this release

- The private target application's bug fixes, complete test runs, production behavior and deploy checks.
- The completeness of raw session accounting; raw transcripts and the session database are private.
- Whether the AI-classified fixes are 77 independently verified bugs, or whether they saved 98 hours.
- A causal improvement from scoping alone: the gate and prompts also changed, with no randomized control.

The paper reports observations by its authors. Publishing scripts and aggregate metadata does
not convert those observations into independent replication.

## Dataset schema

`data.json`: lanes, pass timestamps/outcomes/token counters and commit-level change counts.
`ledger.json`: lane, commit identifier, original review verdict, initial-release disposition and attribution.
`value-labels.json`: AI class, real-bug judgment, severity and estimated engineering hours per commit.
`spotcheck.json`: selection method, blind results and summary; free-text security notes omitted.
`costs.json`: phase-level uncached input, cache read/write, output/reasoning and recorded rates.
`branch-lines.json`: initial-release aggregate additions/deletions, split between production and tests.
`followup.json`: later remediation and verification; not added retroactively to initial-release estimates.

Most commit rows refer to private Git objects. These identifiers let readers join records, not
retrieve source. Security/high-risk subjects are generalized; reviewer free-text reasons and
value-label explanations are omitted. Numeric fields and dispositions are retained.

The factory tests need no provider credentials. PDF builds require downloaded Playwright Chromium.
Font license texts are under `research/fonts/`. The report is not peer-reviewed.
