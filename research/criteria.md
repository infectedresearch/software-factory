# How we judge noise vs value (written before the review results were read)

Written 2026-10-27 ~00:45Z, after the lanes stopped and while the Opus review was still running.
The research page reports against these measures, including the ones that look bad.

## Unit of output
One commit on a lane branch that passed the per-pass gate. 147 commits across 15 branches
(lane 0 = the first single-loop run, lanes 1-14 = parallel lanes).

## Value: a commit counts only if all of these hold
1. The Opus reviewer says keep, or fix and the fix is made (both counted, separately).
2. It applies onto current master and the full test suite shows no new failures versus master.
3. A human (xtat) agrees on read-through to include it. Reviewer verdicts alone do not count.
4. It changes something real: a bug users or operators could hit, a security or safety gap, or a test
   that pins real behavior. Tests that only read source files as text do not count.

Each included commit is sorted into: security/safety fix, user-facing bug fix, reliability fix,
meaningful test, minor.

## Noise
- Dropped by the reviewer, or not included by the human.
- Kept but minor (works, but nobody would have missed it).
- Included and later reverted or found to break something (tracked after deploy).

## Cost
- Writer tokens (Grok) per included commit.
- Reviewer tokens/time (Opus) per included commit, and human review minutes, honestly estimated.
- Setup effort: the factory itself was built during the same session.

## Headline numbers
- Yield = included / produced, by lane and by risk tier.
- Severity mix of included commits.
- Cost per included commit.
- Comparison: lane 0 (one loop, broad prompt) vs lanes 1-14 (parallel, focused areas, stricter gate).

## Known limits we state up front
- One night, one codebase, one writer model; no control group of humans doing the same work.
- The reviewer is also an AI model; the human read is the final judge.
- ~176 tests already fail on master in the worktrees (missing secrets config), so some areas were
  effectively untested by the gate.
- Security-sensitive findings are described by category only until fixed and deployed.
