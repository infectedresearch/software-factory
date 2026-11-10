# Round 2 pre-registration (written before the run started)

## Question

Does giving an unattended coding-agent lane an operator-approved task card
produce more worthwhile integrated work per dollar than letting the lane hunt
freely in an area of the codebase?

## Design

Fourteen lanes, same model (`fracktron/grok-4.7`), same prompt, gate, reviewer
and hours. The 14 round-1 areas were split at random (seed in `design.json`)
into two arms of seven areas:

- **Free arm**: seven lanes, one area each, the round-1 focus text plus a
  "look deeper" note. The lane chooses its own work.
- **Cards arm**: seven lanes sharing a queue of operator-accepted cards drawn
  from the other seven areas. A scout (same model, read-only) proposed cards;
  the operator accepted or declined each on the board before the run. A lane
  takes the oldest accepted card, makes one verified commit, and takes the
  next. Two strikes (empty pass, gate failure or reviewer drop) fail a card.

Both arms run the harness changes listed in `design.json`. The arms differ
only in how a lane's target is chosen.

## Primary outcome

**Cost per integrated non-minor commit**: writing tokens at list price for the
arm, divided by the number of the arm's commits that are integrated onto
master and classified by the reviewer as anything other than `minor`.

## Secondary outcomes

1. Integration rate: integrated commits / commits that passed the gate.
2. Reviewer drop rate and gate rejection rate per arm.
3. Share of integrated commits classified `minor`.
4. Overclaim rate from the blind spot-check (same procedure as round 1).
5. Tokens per landed pass (median), and empty-pass share.
6. Operator time: minutes spent accepting/declining cards (logged by hand),
   charged to the cards arm.
7. For the cards arm only: cards accepted, built, failed, and the share of
   built cards whose commit the reviewer kept.

## What would count as a result

A difference of 30% or more in the primary outcome, in either direction, with
at least 40 gate-passed commits per arm. Below that, the honest report is
"no detectable difference at this size".

## Known limits, stated up front

- One night, one codebase, one model. Seven lanes per arm.
- Areas were randomized, but each area appears in exactly one arm, so an
  unusually rich or poor area moves that arm's numbers.
- The card lanes' areas were scouted; the free lanes' areas were not. The
  scout's token cost is charged to the cards arm.
- Value classes and spot-check are AI judgements, as in round 1.
- Round-1 fixes removed the easiest bugs from every area; absolute yields are
  expected to be lower than round 1 in both arms.
