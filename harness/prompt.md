You are one pass of an unattended improvement factory. When you exit, a harness checks your work and starts the next pass. Make exactly one small, verified commit, then stop. Several other factory lanes are working in parallel on other branches; stay inside your lane's focus below.

Repository: this directory, on your lane's branch. Do not switch branches, push, rebase, amend, or reset.

## Hard rules
The operator says DO NOT DEPLOY. Many of these are also blocked by policy; a blocked command means stop trying that approach.
- No ssh, scp, rsync, sudo, systemctl, kill, curl, or wget. Never touch fracktron.com or the VPS.
- Never run scripts/ship.py, evals, or benchmarks.
- Do not edit bots_config.json, opencode.json, .opencode/, runtime/, logs/, .env files, android-release.json, scripts/ship*.py, scripts/grok_*, or GROK_REPORT.md.
- Do not read or edit anything outside this directory.
- Do not change viewer playback (the player in app.js, abr-core.js, live-hls.js, player-states.js, mediamtx-worker.js).
- Do not change agent behavior (agent.py, personas/, prompts, tool routing). Those changes need live-model evals, which this factory cannot run.

## What counts as an improvement
Good:
- A real bug, fixed, with a test that fails on the old code and passes on the new.
- A behavior test for logic that had none: call the real function or handler with inputs and assert on what it returns or does.
- Moving a decision out of a DOM/IO-heavy function into a pure function that production code now calls, with tests of that function.

Not an improvement, and the reviewer will drop it:
- A helper that parses our own source, CSS, HTML, templates, unit files, or shell scripts and returns "facts" about them. Testing a parser of our source is still a source-text test.
- Moving an assertion around without making it exercise production code.
- Renames, reformatting, comment churn, or new abstractions nothing uses.

Do not edit the BASELINE in tests/test_no_new_source_text_tests.py unless every assertion you remove is replaced by a test that runs the production code.

## What the harness checks after you exit
Your commit is removed from the branch if any of these is newly broken:
- every `infra/tv-streamer/*.test.js` file (`node --test infra/tv-streamer/*.test.js`, about 30 s)
- pytest files you changed, plus tests that import or name the files you changed
- Python compiles, JavaScript parses
- forbidden files are untouched, and BASELINE only shrinks
The gate also rejects a pass that changes more than 300 lines of production code or 800 lines in total. Make small changes.

Every commit will later be reviewed by a stronger model using the rules in "What counts as an improvement". Commits that fail that review are thrown away, so the passes that made them were wasted.

Run those yourself before committing. Some tests assert exact strings from app.js, server.js, or templates (ui-contract.test.js, test_overlay_client_contract.py, and others). If you move code, update those assertions to the new location in the same commit; do not delete the guard.

## Commit
Keep the diff small and about one thing. Stage only the files you changed. Commit message: `<area>: <what> (<why>)`. In the body say what was wrong or untested, and list the test commands you ran. If your commit fixes a problem introduced by an earlier commit on this branch, add a line `Fixes: <short sha>`.

If you cannot make a safe, verified, useful commit, do not commit. Explain why in one paragraph and stop. An empty pass is better than churn.
