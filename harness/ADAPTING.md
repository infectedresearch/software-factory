# Adapting the research harness

The snapshot was exported from the operational factory, not reimplemented for this release.
Absolute operator paths were replaced with `/srv/factory/...` placeholders. No production lane
environment files or service units are shipped. The original model identifiers are historical
labels; select models available from your own provider.

1. Use a disposable target clone on an isolated worker with only the credentials it needs.
   Keep the factory outside that clone. Run the included synthetic tests first.
2. Inspect `config.py`, `loop.sh`, `gate.py`, `integrate.py`, `assemble.py`, `review.py` and
   `status.py`. Replace path defaults, the target branch, Node/Python test selectors, forbidden
   paths, and project-specific deployment classifications. `FACTORY_WORKTREE`, `FACTORY_BRANCH`,
   `FACTORY_STATE`, `FACTORY_SCRATCH`, `FACTORY_MODEL`, `FACTORY_REVIEWER`, `FACTORY_FOCUS`,
   `FACTORY_HOURS` and `FACTORY_PASS_TIMEOUT` control the corresponding loop settings.
3. Create a dedicated worktree/branch per lane and a matching `lanes/<id>.env` file for
   review/status discovery. Set `FACTORY_LANE`, worktree, branch, state and focus in each file.
   Lane files are parsed as plain `KEY=value`, without shell expansion. Start with one lane.
4. Rewrite `prompt.md` for your project and provide a narrow focus file. The recorded loop uses
   OpenCode `run --standalone --auto`; check your installed `opencode run --help` and provider setup.
   The included policy is the historical configuration, not a verified portable security boundary.
5. Set a short deadline, then explicitly launch `bash loop.sh` with your lane environment.
   To stop after the current pass, create `<state>/stop`. Six consecutive empty passes also stop it.
6. Inspect gate reports and review commits before integration. Keep inline review off unless
   explicitly enabled. Integration and deployment are separate operator actions.

The board's authentication adapter expects an application session service and database; replace
it before running the board. It is not needed to run the tests or inspect the report.

Known limitations: command patterns can be bypassed through arbitrary code; worktrees share
Git metadata and stashes; the related-test heuristic can miss regressions; reviewer models
can share blind spots; a green gate does not establish product value. An external sandbox,
resource limits, full-suite comparisons and human review are separate controls.
