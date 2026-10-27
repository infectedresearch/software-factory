# Software Factory

**An overnight experiment in unattended software development—and the review work needed to make its output useful.**

[Research landing page](https://fracktron.com/research/) · [Read the paper](https://fracktron.com/research/software-factory/) · [PDF](https://fracktron.com/research/software-factory/software-factory-technical-report.pdf)

Infected Research / FrackLabs · Technical report FL-TR-2026-01 · October 2026

## Results, with the denominators attached

| Initial-release measure | Result |
|---|---:|
| Coding agents / run window | 14 / approximately 4.5 hours |
| Agent passes / factory commits | 186 / 132 |
| Factory commits integrated after review | 110 |
| Tokens, including repeated cached context | 848 million |
| Snapshot cost at recorded list prices | approximately $493 |
| Non-minor bug-fix commits, AI-classified | 77 |
| Estimated equivalent engineer effort, AI-estimated | approximately 98 hours |
| Broad-loop / scoped-lane original keep rates | 30% / 89% |
| Blind spot-check agreement on keep | 11 of 12 |
| Python baseline / candidate failures | 181 / 174; zero new failures |
| Node tests passing | 1,123 |

The central finding: producing commits was easier than deciding which ones deserved to ship.
The scoped runs also used a stricter gate; the keep-rate comparison is observational, not a causal experiment.

**This is a preliminary, single-codebase field report, not a controlled productivity benchmark.**
The 77-fix and 98-hour figures are model judgments, not independent measurements or payroll savings.
The cost is a frozen accounting snapshot, not the complete cost of later remediation, publication,
infrastructure, or human time. Tokens include cache reads, not 848 million newly generated tokens.

## What's here

- `harness/`: the actual factory orchestration, regression-relative gate, review prompts,
  integration tools, board, and synthetic factory tests, with host-local paths replaced.
- `research/`: the paper, figures, build scripts, pre-established value criteria,
  aggregate accounting, per-pass timings, 147-row review ledger, AI labels, and blind spot-check results.
- `state/final-suite.json`: aggregate baseline-versus-candidate validation results.
- `verify.py`: recompute the headline counts and validate the published data relationships.
- `REPRODUCIBILITY.md`: what can be independently reproduced and what cannot.

**The application studied is private.** No application source, application patches, raw agent
transcripts, production database, credentials, or target-repository Git history is included.
Commit IDs are record identifiers, not links to public application code. Sensitive metadata
is category-only; private review reasoning is omitted.

## Try the artifacts without an API key

Requires Python 3.11+, Node.js 20+, Git, and `uv` for the factory test runner.

```sh
python3 verify.py
python3 research/build.py
uv run --with pytest pytest -q harness/tests
```

The tests create disposable synthetic Git repositories. They exercise the real factory gate,
including rejecting newly introduced failures while tolerating known baseline failures.
They do not call a model, modify your repositories, or deploy anything.

Optional PDF rebuild:

```sh
npm ci
npx playwright install chromium
python3 research/paper.py
```

The bundled PDF is the reviewed publication. Rebuilding uses the more conservatively redacted
public ledger, so appendix wording and pagination can differ, while headline values remain the same.

## Running a factory against your own project

Start with [harness/ADAPTING.md](harness/ADAPTING.md). This is the research implementation,
not a turnkey multi-tenant service. The test selectors, deployment labels, model IDs and
dashboard authentication reflect the studied system and need adapters for another project.
The command-pattern policy is **not an OS sandbox**. No lanes or background jobs start on installation.

## Follow-up fixes

Seven content-filter findings were initially only expected-failure tests. They were subsequently
fixed and deployed. A held tips-recipient defect was also fixed with an indexed lookup and
ambiguous-identity rejection. Related checks passed: 378 filter/manager tests, 351 database/tips
tests and 21 migration tests. These follow-ups are recorded separately from initial-release totals.

## Contributing

We welcome independent replications, stronger evaluation methods, portable project adapters,
and corrections to the arithmetic. Include denominators and distinguish model judgments from
measured outcomes. Please do not put private code or credentials in issues.

Code: MIT. Original paper, datasets and figures: CC BY 4.0. Fonts retain their SIL OFL licenses.
See `CITATION.cff` for attribution. No endorsement by the model or tool providers is implied.
