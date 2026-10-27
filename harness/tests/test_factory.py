from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import config as C
import gate
import status

OLD_LOG = """\
2026-10-26T19:49:26Z start model=fracktron/grok-4.7 hours=12 head=695bc514
2026-10-26T19:49:26Z iteration 1 begin 695bc5148da61dc356f6e2a9de4375c43ec0114f
2026-10-26T20:01:01Z iteration 1 opencode_exit=0 head=b9fd7fdd
2026-10-26T20:01:01Z iteration 1 regression
2026-10-26T20:01:05Z regression passed at b9fd7fdd
"""

NEW_LOG = """\
2026-10-26T21:00:00Z start model=fracktron/grok-4.7 until=2026-10-27T08:30:00Z head=aaaaaaa
2026-10-26T21:00:01Z pass 9 begin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa model=fracktron/grok-4.7
2026-10-26T21:09:00Z pass 9 end exit=0 head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
2026-10-26T21:09:00Z pass 9 gate start
2026-10-26T21:11:00Z pass 9 gate failed secs=118
2026-10-26T21:11:00Z pass 9 rejected kept=factory/rejected/pass-9
2026-10-26T21:11:01Z pass 10 begin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa model=fracktron/grok-4.7
2026-10-26T21:15:00Z pass 10 end exit=0 head=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
2026-10-26T21:15:00Z pass 10 no-commit (1/6)
2026-10-26T21:15:01Z pass 11 begin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa model=fracktron/grok-4.7
> build · grok-4.7
I'll move the hero picker into a pure function so the test can run it.
← Edit infra/landing/assets/home.js
Index: infra/landing/assets/home.js
+  const Answer = computeTheThing(value, other, more, words);
Error: Connection lost while reading the response: ECONNRESET: The socket closed unexpectedly.
→ Read infra/tv-streamer/watch-home.js [limit=80]
$ node --test infra/tv-streamer/watch-home.test.js
"""


def test_old_and_new_log_lines_become_passes_with_outcomes():
    parsed = status.parse_log(OLD_LOG + NEW_LOG)
    by_n = {p["n"]: p for p in parsed["passes"]}
    assert status.outcome(by_n[1], alive=True) == "landed"
    assert status.outcome(by_n[9], alive=True) == "rejected"
    assert by_n[9]["gate_secs"] == "118"
    assert status.outcome(by_n[10], alive=True) == "empty"
    assert status.outcome(by_n[11], alive=True) == "running"
    assert status.outcome(by_n[11], alive=False) == "interrupted"
    assert parsed["runs"][-1]["until"] == "2026-10-27T08:30:00Z"


def test_current_intent_is_prose_not_code_diffs_or_errors():
    parsed = status.parse_log(NEW_LOG)
    assert parsed["intent"] == "I'll move the hero picker into a pure function so the test can run it."
    kinds = [s["kind"] for s in parsed["steps"]]
    assert kinds == ["edit", "read", "test"]
    assert parsed["steps"][0]["text"] == "Edited infra/landing/assets/home.js"


@pytest.mark.parametrize(
    "line",
    [
        "const answer = computeTheThing(value, other, more, words);",
        "+  assert.equal(result.rung, 480, 'the rung must be four eighty');",
        "AssertionError [ERR_ASSERTION]: auth/status success path must paint identity immediately.",
        "Index: infra/landing/assets/home.js and some more words here.",
    ],
)
def test_code_and_test_output_are_not_mistaken_for_intent(line):
    assert status.is_sentence(line) is False


def test_forbidden_paths_and_baseline_growth():
    assert gate.forbidden(["bots_config.json", "infra/tv-streamer/app.js", "scripts/grok_x.sh", ".opencode/a"]) == [
        "bots_config.json",
        "scripts/grok_x.sh",
        ".opencode/a",
    ]
    before = 'BASELINE: frozenset[str] = frozenset({"a.test.js", "b.py"})'
    after = 'BASELINE: frozenset[str] = frozenset({"a.test.js", "c.py"})'
    assert gate.baseline_set(after) - gate.baseline_set(before) == {"c.py"}


def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "f@example.com")
    _git(repo, "config", "user.name", "factory")
    return repo


def _commit(repo: Path, rel: str, text: str, message: str) -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


PASSING = "require('node:test')('ok', () => {});\n"
FAILING = "require('node:test')('bad', () => { throw new Error('boom'); });\n"


def test_gate_rejects_a_new_failure_but_not_an_old_one(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    _commit(repo, "infra/tv-streamer/old.test.js", FAILING, "already broken")
    base = _commit(repo, "infra/tv-streamer/new.test.js", PASSING, "base")
    monkeypatch.setattr(C, "SCRATCH", tmp_path / "scratch")

    _commit(repo, "infra/tv-streamer/other.js", "const x = 1;\n", "harmless")
    clean = gate.gate(repo, base)
    assert clean["passed"] is True
    assert clean["preexisting"] == ["infra/tv-streamer/old.test.js"]

    _commit(repo, "infra/tv-streamer/new.test.js", FAILING, "break it")
    broken = gate.gate(repo, base)
    assert broken["passed"] is False
    assert any("new.test.js" in reason for reason in broken["reasons"])
    assert not any("old.test.js" in reason for reason in broken["reasons"])


def test_gate_rejects_forbidden_files_and_bad_syntax(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _commit(repo, "README", "x\n", "base")
    monkeypatch.setattr(C, "SCRATCH", tmp_path / "scratch")
    _commit(repo, "bots_config.json", "{}\n", "touch config")
    _commit(repo, "lib/broken.js", "function (\n", "bad js")
    report = gate.gate(repo, base)
    assert report["passed"] is False
    joined = " ".join(report["reasons"])
    assert "bots_config.json" in joined
    assert "lib/broken.js" in joined


@pytest.fixture()
def review_repo(tmp_path, monkeypatch):
    main = _repo(tmp_path)
    _commit(main, "app.txt", "base\n", "base")
    feature = tmp_path / "feature"
    _git(main, "worktree", "add", "-q", "-b", "grok/improvements", str(feature))
    monkeypatch.setattr(C, "SKIPFILE", tmp_path / "skipped.txt")
    monkeypatch.setattr(C, "OLD_SKIPFILE", tmp_path / "old-skipped.txt")
    monkeypatch.setattr(C, "INCLUDED", tmp_path / "included.jsonl")
    monkeypatch.setattr(C, "VERDICTS", tmp_path / "verdicts.jsonl")
    return main, feature


def test_gate_rejects_a_change_too_big_to_review(tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    base = _commit(repo, "README", "x\n", "base")
    monkeypatch.setattr(C, "SCRATCH", tmp_path / "scratch")
    _commit(repo, "lib/big.py", "".join(f"value_{i} = {i}\n" for i in range(400)), "big")
    report = gate.gate(repo, base)
    assert report["passed"] is False
    assert any("Too big to review" in reason for reason in report["reasons"])
    assert report["lines"] == {"production": 400, "total": 400}


def test_reviewer_outcomes_in_the_log():
    log = """\
2026-10-26T23:00:00Z pass 3 begin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa model=m
2026-10-26T23:05:00Z pass 3 end exit=0 head=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
2026-10-26T23:05:00Z pass 3 gate start
2026-10-26T23:06:00Z pass 3 gate passed secs=40
2026-10-26T23:06:00Z pass 3 review start model=r
2026-10-26T23:08:00Z pass 3 review drop bbbbbbb
2026-10-26T23:08:00Z pass 3 dropped kept=factory/dropped/lane-2-pass-3
2026-10-26T23:08:01Z pass 4 begin aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa model=m
2026-10-26T23:12:00Z pass 4 end exit=0 head=cccccccccccccccccccccccccccccccccccccccc
2026-10-26T23:12:00Z pass 4 gate start
2026-10-26T23:13:00Z pass 4 gate passed secs=40
2026-10-26T23:13:00Z pass 4 review start model=r
2026-10-26T23:15:00Z pass 4 review fix ccccccc
2026-10-26T23:15:01Z pass 5 begin cccccccccccccccccccccccccccccccccccccccc model=m
2026-10-26T23:19:00Z pass 5 end exit=0 head=dddddddddddddddddddddddddddddddddddddddd
2026-10-26T23:19:00Z pass 5 gate start
2026-10-26T23:20:00Z pass 5 gate passed secs=40
2026-10-26T23:20:00Z pass 5 review start model=r
"""
    by_n = {p["n"]: p for p in status.parse_log(log)["passes"]}
    assert status.outcome(by_n[3], alive=True) == "dropped"
    assert status.outcome(by_n[4], alive=True) == "needs-fix"
    assert status.outcome(by_n[5], alive=True) == "reviewing"


def test_include_kept_takes_kept_skips_dropped_and_stops_at_unreviewed(review_repo):
    main, feature = review_repo
    kept = _commit(feature, "a.txt", "a\n", "kept change")
    dropped = _commit(feature, "b.txt", "b\n", "dropped change")
    _commit(feature, "c.txt", "c\n", "unreviewed change")
    C.VERDICTS.write_text(
        '{"sha": "%s", "verdict": "keep", "reason": "good"}\n{"sha": "%s", "verdict": "drop", "reason": "churn"}\n'
        % (kept, dropped)
    )
    result = status.include_kept(feature)
    assert result["ok"] is True
    assert len(result["included"]) == 1 and (main / "a.txt").is_file()
    assert result["skipped"] == [dropped[:7]] or result["skipped"][0].startswith(dropped[:7])
    assert not (main / "b.txt").exists()
    assert "unreviewed change" in result["stopped_at"] and "not reviewed yet" in result["stopped_at"]


def test_lessons_keep_the_latest_objections(tmp_path):
    import review

    lane = C.Lane("lane-9", tmp_path, "b", tmp_path / "state", "t", tmp_path / "focus.md")
    for i in range(10):
        review.add_lesson(lane, {"short": f"s{i}", "subject": f"change {i}", "verdict": "drop", "reason": f"reason {i}"})
    text = review.lessons_text(lane)
    assert "change 9" in text and "change 2" in text and "change 1 " not in text


BASELINE_TEXT = 'BASELINE = frozenset(\n    {\n        "a.py",\n        "b.py",\n        "c.py",\n        "d.py",\n    }\n)\n'


def test_neighbouring_baseline_deletions_merge_and_other_conflicts_abort(tmp_path):
    import picking

    repo = _repo(tmp_path)
    _commit(repo, picking.BASELINE_FILE, BASELINE_TEXT, "base")
    _git(repo, "checkout", "-q", "-b", "lane")
    lane_sha = _commit(repo, picking.BASELINE_FILE, BASELINE_TEXT.replace('        "c.py",\n', ""), "drop c")
    other_sha = _commit(repo, "x.txt", "lane\n", "lane edits x")
    _git(repo, "checkout", "-q", "master")
    _commit(repo, picking.BASELINE_FILE, BASELINE_TEXT.replace('        "b.py",\n', ""), "drop b")
    _commit(repo, "x.txt", "master\n", "master edits x")

    merged = picking.cherry_pick(repo, lane_sha)
    assert merged == {"ok": True, "resolved": True, "files": [], "error": ""}
    text = (repo / picking.BASELINE_FILE).read_text()
    assert '"b.py"' not in text and '"c.py"' not in text and '"a.py"' in text and '"d.py"' in text

    head = _git(repo, "rev-parse", "HEAD")
    refused = picking.cherry_pick(repo, other_sha)
    assert refused["ok"] is False and refused["files"] == ["x.txt"]
    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "status", "--porcelain") == ""


def test_integration_bisects_a_combined_failure_to_the_lane_commit(tmp_path, monkeypatch):
    import integrate

    repo = _repo(tmp_path)
    _commit(repo, "check.test.js", "require('node:test')('ok', () => {});\n", "base test")
    _git(repo, "checkout", "-q", "-b", "lane")
    good = _commit(repo, "notes.txt", "fine\n", "harmless")
    bad = _commit(repo, "check.test.js", "require('node:test')('bad', () => { throw new Error('x'); });\n", "breaks it")
    _git(repo, "checkout", "-q", "master")
    checkout = tmp_path / "integration"
    _git(repo, "worktree", "add", "-q", "--detach", str(checkout), "master")
    for sha in (good, bad):
        _git(checkout, "cherry-pick", "-x", sha)
    monkeypatch.setattr(integrate, "CHECKOUT", checkout)
    applied = [{"lane": "lane-3", "sha": s, "short": s[:7], "subject": m} for s, m in ((good, "harmless"), (bad, "breaks it"))]
    blamed = integrate.attribute(["check.test.js"], applied)
    assert [(b["lane"], b["sha"]) for b in blamed] == [("lane-3", bad)]

    lane = C.Lane("lane-3", checkout, "lane", tmp_path / "state3", "t", tmp_path / "f.md")
    monkeypatch.setattr(C, "lanes", lambda directory=None: [lane])
    integrate.write_feedback(blamed)
    assert "breaks check.test.js" in (lane.state / "feedback.txt").read_text()
    integrate.write_feedback([])
    assert not (lane.state / "feedback.txt").exists()


def test_reviewer_verdict_is_read_from_its_last_line():
    import review

    text = 'thinking about it\nVERDICT {"verdict": "fix", "reason": "Reads config before boot sets it."}\n'
    assert review.parse_verdict(text) == {"verdict": "fix", "reason": "Reads config before boot sets it."}
    assert review.parse_verdict('VERDICT {"verdict": "maybe"}') is None
    assert review.parse_verdict("no verdict here") is None


def test_lane_files_describe_each_parallel_loop(tmp_path):
    (tmp_path / "lane-2.env").write_text(
        "FACTORY_LANE=lane-2\nFACTORY_WORKTREE=/w/lane-2\nFACTORY_BRANCH=grok/lane-2\n"
        "FACTORY_STATE=/s/lane-2\nFACTORY_TITLE=lane2-improve\n"
    )
    (lane,) = C.lanes(tmp_path)
    assert lane.id == "lane-2" and lane.branch == "grok/lane-2"
    assert str(lane.log) == "/s/lane-2/loop.log"
    assert lane.unit == "grok-factory-lane@lane-2.service"


def test_smart_review_verdicts_show_on_waiting_commits(review_repo):
    _, feature = review_repo
    first = _commit(feature, "app.txt", "one\n", "first change")
    C.VERDICTS.write_text('{"sha": "%s", "verdict": "drop", "reason": "parses source"}\n' % first)
    (item,) = status.review_state(feature)["items"]
    assert item["verdict"]["verdict"] == "drop"


def test_review_is_oldest_first_and_include_copies_to_master(review_repo):
    main, feature = review_repo
    first = _commit(feature, "app.txt", "one\n", "first change")
    _commit(feature, "scripts/grok_loop.sh", "echo\n", "factory tooling only")
    second = _commit(feature, "other.txt", "two\n", "second change\n\nFixes: " + first[:8])
    state = status.review_state(feature)
    assert [i["subject"] for i in state["items"]] == ["first change", "second change"]
    assert state["hidden_tooling"] == 1
    assert state["items"][0]["fixed_by"] == [second[:7]] or state["items"][0]["fixed_by"][0].startswith(second[:7])

    refused = status.include(feature, second)
    assert refused["ok"] is False and "older commit first" in refused["error"]

    done = status.include(feature, first)
    assert done["ok"] is True
    assert (main / "app.txt").read_text() == "one\n"
    # The copy on master has a new sha; the queue must still drop the original.
    assert [i["subject"] for i in status.review_state(feature)["items"]] == ["second change"]


def test_include_refuses_and_leaves_master_alone_when_master_has_local_edits(review_repo):
    main, feature = review_repo
    first = _commit(feature, "app.txt", "one\n", "first change")
    (main / "app.txt").write_text("someone else's edit\n")
    result = status.include(feature, first)
    assert result["ok"] is False
    assert (main / "app.txt").read_text() == "someone else's edit\n"
    assert _git(main, "log", "-1", "--format=%s") == "base"


def test_effects_say_what_including_changes_on_the_live_lab():
    texts = lambda files: [e["text"] for e in status.effects(files)]  # noqa: E731
    assert any("ship.py" in t for t in texts(["infra/tv-streamer/app.js"]))
    assert any("restart it right after" in t for t in texts(["sketchpad.html", "overlay_utility.js"]))
    assert any("live as soon as" in t for t in texts(["auth/templates/base.html"]))
    assert texts(["tests/test_x.py", "infra/tv-streamer/a.test.js"]) == ["Tests or docs only: nothing live changes"]


def test_skip_hides_a_commit(review_repo):
    _, feature = review_repo
    first = _commit(feature, "app.txt", "one\n", "first change")
    assert status.skip(first)["ok"] is True
    assert status.review_state(feature)["items"] == []
    assert status.skip("not-a-sha")["ok"] is False
