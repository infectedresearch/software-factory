"""Value-classify round 2's surviving commits, blind to arm.

Each batch gets commits in random order with lane, arm and card stripped,
plus the commit message, diff (truncated) and the inline reviewer's reason.
Labels use round 1's schema: class, real_bug, severity, eng_hours, why.

    python3 research/round2/classify.py [--model fracktron/claude-opus-5-5]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

OUT = Path(__file__).parent
REPO = Path("/srv/factory/target")
WORK = Path("/srv/factory/lanes/scout")
SEED = 20261110
BATCH = 8

PROMPT = """You are classifying commits from an unattended coding-agent experiment for a research report. Judge each commit on what it actually changes, using the diff, not on how the message describes it. Commit messages from these agents tend to overclaim.

For each commit, assign:
- class: exactly one of
  security-or-safety  (closes a permission, validation, injection, secret-handling or abuse-limit gap in a real path)
  user-facing-bug     (fixes behaviour a real user can trigger)
  reliability         (fixes a crash, hang, unbounded growth, swallowed error or missing timeout in a long-running path)
  meaningful-test     (adds a behaviour test of a real production decision that had none; no production change needed)
  minor               (works, but nobody would have missed it: cosmetic, defensive code for a state no input reaches, tests of trivial glue)
- real_bug: true only if the diff fixes behaviour a real input can trigger on the old code. A test-only commit is false.
- severity: low | medium | high (impact if the bug had shipped or stayed; "low" for tests and minor)
- eng_hours: rough hours a senior engineer unfamiliar with this exact code would need to find, fix and test it (integer, 1 minimum)
- why: one sentence, plain language

You may read files in this directory to check a claim, but do not edit anything or run commands that change files.

Return ONLY a JSON array, one object per commit, in any order:
[{{"sha": "<short sha as given>", "class": "...", "real_bug": true, "severity": "...", "eng_hours": 1, "why": "..."}}]

Commits:
{commits}"""


def diff(sha: str, limit: int = 220) -> str:
    text = subprocess.run(["git", "-C", str(REPO), "show", "--format=", "--unified=3", sha],
                          capture_output=True, text=True).stdout.splitlines()
    return "\n".join(text[:limit]) + (f"\n... ({len(text) - limit} more diff lines)" if len(text) > limit else "")


def render(c: dict) -> str:
    return (f"=== {c['short']}\nSubject: {c['subject']}\n{c['body']}\n"
            f"Inline reviewer said: {c.get('reason') or '(not reviewed)'}\n--- diff ---\n{diff(c['sha'])}\n")


def parse(text: str) -> list[dict]:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    best: list[dict] = []
    for m in re.finditer(r"\[\s*\{", text):
        depth = 0
        for i in range(m.start(), len(text)):
            depth += {"[": 1, "]": -1}.get(text[i], 0)
            if depth == 0:
                try:
                    data = json.loads(text[m.start():i + 1])
                    if isinstance(data, list) and len(data) > len(best):
                        best = data
                except json.JSONDecodeError:
                    pass
                break
    return best


def run_batch(i: int, batch: list[dict], model: str) -> list[dict]:
    log = OUT / "classify" / f"batch-{i:02d}.log"
    prompt = PROMPT.format(commits="\n".join(render(c) for c in batch))
    with log.open("w") as fh:
        subprocess.run(["opencode", "run", "--standalone", "--auto", "--model", model, "--title", f"classify-r2-{i:02d}", prompt],
                       cwd=WORK, stdout=fh, stderr=subprocess.STDOUT, timeout=1800, check=False,
                       env={**__import__("os").environ, "PWD": str(WORK), "TMPDIR": "/srv/factory/operator/tmp"})
    wanted = {c["short"] for c in batch}
    return [r for r in parse(log.read_text()) if r.get("sha") in wanted]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="fracktron/claude-opus-5-5")
    ap.add_argument("--parallel", type=int, default=6)
    a = ap.parse_args()
    (OUT / "classify").mkdir(exist_ok=True)
    rows = json.loads((OUT / "commits.json").read_text())
    done = {}
    labels_path = OUT / "value-labels.json"
    if labels_path.is_file():
        done = {r["sha"]: r for r in json.loads(labels_path.read_text())}
    todo = [r for r in rows if r["short"] not in done]
    random.Random(SEED).shuffle(todo)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    with ThreadPoolExecutor(a.parallel) as pool:
        for got in pool.map(lambda ib: run_batch(ib[0], ib[1], a.model), enumerate(batches)):
            for r in got:
                done[r["sha"]] = {**r, "model": a.model}
    labels_path.write_text(json.dumps(list(done.values()), indent=1))
    missing = [r["short"] for r in rows if r["short"] not in done]
    print(f"{len(done)} labelled; missing {len(missing)}: {missing[:20]}")


if __name__ == "__main__":
    main()
