#!/usr/bin/env python3
"""Quality gate for one factory pass.

    gate.py --base <sha> [--out result.json]

Checks the worktree's HEAD against ``base``:

* no forbidden paths changed (config, secrets, deploy tooling, factory files)
* the source-text BASELINE only shrinks
* changed Python compiles, changed JavaScript parses
* every infra/tv-streamer node test file passes, plus any other changed
  ``*.test.js``
* pytest files related to the change pass (changed tests, and tests that
  import or name a changed file)

A failure only counts if it is new: each failing node file or pytest id is
re-run at ``base`` in a scratch checkout. Failures that were already there
are reported but do not reject the pass.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import config as C

PYTEST_CAP = 60
CORE_PYTESTS = (
    "tests/test_no_new_source_text_tests.py",
    "tests/test_twitter_invite_defaults.py",
    "tests/test_legacy_overlay_utilities.py",
    "tests/test_dashboard_scroll.py",
    "tests/test_share_parity.py",
    "tests/test_overlay_client_contract.py",
)
EXTRA_NODE_TESTS = ("overlay_utility.test.js", "auth/static/dashboard-scroll.test.js")
SKIP_NODE_CHECK = re.compile(r"(\.min\.js$|-vendor\.js$|/frackworld\.js$|/node_modules/)")


def run(cmd: list[str], cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return subprocess.CompletedProcess(cmd, 124, out, f"timed out after {timeout}s")


def git(repo: Path, *args: str) -> str:
    return run(["git", *args], repo, 120).stdout


def changed_files(repo: Path, base: str) -> list[tuple[str, str]]:
    rows = git(repo, "diff", "--name-status", "--no-renames", f"{base}..HEAD")
    out = []
    for line in rows.splitlines():
        status, _, path = line.partition("\t")
        if path:
            out.append((status.strip(), path.strip()))
    return out


MAX_PRODUCTION_LINES = 300
MAX_TOTAL_LINES = 800
TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|\.test\.[cm]?js$|\.spec\.[cm]?js$")


def diff_size(repo: Path, base: str) -> tuple[int, int]:
    """(production lines changed, total lines changed) between base and HEAD."""
    production = total = 0
    for line in git(repo, "diff", "--numstat", f"{base}..HEAD").splitlines():
        added, deleted, path = (line.split("\t") + ["", "", ""])[:3]
        if not added.isdigit() or not deleted.isdigit():
            continue
        changed = int(added) + int(deleted)
        total += changed
        if not TEST_PATH.search(path):
            production += changed
    return production, total


def forbidden(paths: list[str]) -> list[str]:
    pats = [re.compile(p) for p in C.FORBIDDEN_PATTERNS]
    return [p for p in paths if any(r.search(p) for r in pats)]


def baseline_set(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target, value = node.target.id, node.value
        elif isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            target, value = node.targets[0].id, node.value
        if target == "BASELINE" and value is not None:
            return {
                n.value
                for n in ast.walk(value)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
    return set()


def baseline_growth(repo: Path, base: str) -> list[str]:
    path = "tests/test_no_new_source_text_tests.py"
    before = baseline_set(git(repo, "show", f"{base}:{path}"))
    try:
        after = baseline_set((repo / path).read_text())
    except OSError:
        return []
    return sorted(after - before)


def related_pytests(repo: Path, paths: list[str]) -> list[str]:
    tests_dir = repo / "tests"
    picked: list[str] = []

    def add(rel: str) -> None:
        if rel not in picked and (repo / rel).is_file():
            picked.append(rel)

    for rel in CORE_PYTESTS:
        add(rel)
    for rel in paths:
        if re.match(r"^(tests|public_api/tests)/test_.*\.py$", rel):
            add(rel)
    needles: list[str] = []
    for rel in paths:
        p = Path(rel)
        if p.suffix == ".py" and not p.name.startswith("test_"):
            dotted = ".".join(p.with_suffix("").parts)
            needles.append(rf"(import|from)\s+{re.escape(dotted)}\b")
            if len(p.parts) > 1:
                pkg = ".".join(p.parts[:-1])
                needles.append(rf"from\s+{re.escape(pkg)}\s+import\s+[^\n]*\b{re.escape(p.stem)}\b")
        elif p.suffix in {".html", ".js", ".css", ".sh"} and not p.name.endswith(".test.js"):
            needles.append(re.escape(p.name))
    if needles and tests_dir.is_dir():
        pattern = re.compile("|".join(needles))
        for test in sorted(tests_dir.glob("test_*.py")):
            if len(picked) >= PYTEST_CAP:
                break
            try:
                if pattern.search(test.read_text(errors="replace")):
                    add(str(test.relative_to(repo)))
            except OSError:
                continue
    return picked[:PYTEST_CAP]


def node_test_files(repo: Path, paths: list[str]) -> list[str]:
    files = sorted(str(p.relative_to(repo)) for p in (repo / "infra/tv-streamer").glob("*.test.js"))
    for rel in (*EXTRA_NODE_TESTS, *paths):
        if rel.endswith(".test.js") and (repo / rel).is_file() and rel not in files:
            files.append(rel)
    return files


def failing_node_files(repo: Path, files: list[str]) -> tuple[list[str], str]:
    if not files:
        return [], ""
    batch = run(["node", "--test", *files], repo, 900)
    if batch.returncode == 0:
        return [], ""
    failed = []
    for rel in files:
        if run(["node", "--test", rel], repo, 300).returncode != 0:
            failed.append(rel)
    tail = (batch.stdout + batch.stderr)[-4000:]
    return failed, tail


PYTEST_FAIL = re.compile(r"^(FAILED|ERROR) (\S+)")


def failing_pytests(repo: Path, files: list[str]) -> tuple[list[str], str]:
    if not files:
        return [], ""
    result = run(
        ["uv", "run", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *files],
        repo,
        1200,
    )
    if result.returncode == 0:
        return [], ""
    ids = []
    for line in result.stdout.splitlines():
        match = PYTEST_FAIL.match(line.strip())
        if match:
            ids.append(match.group(2))
    if not ids and result.returncode not in (0, 5):
        ids = ["<pytest did not run: exit %d>" % result.returncode]
    return ids, (result.stdout + result.stderr)[-4000:]


def scratch_at(repo: Path, sha: str) -> Path:
    if not (C.SCRATCH / ".git").exists():
        run(["git", "worktree", "add", "--detach", str(C.SCRATCH), sha], repo, 300)
    run(["git", "checkout", "--force", "--detach", sha], C.SCRATCH, 300)
    run(["git", "clean", "-fdq", "-e", ".venv"], C.SCRATCH, 300)
    return C.SCRATCH


def new_node_failures(repo: Path, base: str, failed: list[str]) -> list[str]:
    if not failed:
        return []
    confirmed = [rel for rel in failed if run(["node", "--test", rel], repo, 300).returncode != 0]
    if not confirmed:
        return []
    scratch = scratch_at(repo, base)
    fresh = []
    for rel in confirmed:
        if not (scratch / rel).is_file() or run(["node", "--test", rel], scratch, 300).returncode == 0:
            fresh.append(rel)
    return fresh


def new_pytest_failures(repo: Path, base: str, failed: list[str]) -> list[str]:
    if not failed:
        return []
    real = [i for i in failed if not i.startswith("<")]
    if len(real) != len(failed):
        return failed
    again, _ = failing_pytests(repo, real)
    if not again:
        return []
    scratch = scratch_at(repo, base)
    fresh = []
    for test_id in again:
        file_part = test_id.split("::", 1)[0]
        if not (scratch / file_part).is_file():
            fresh.append(test_id)
            continue
        result = run(["uv", "run", "pytest", "-q", "-p", "no:cacheprovider", test_id], scratch, 600)
        if result.returncode == 0:
            fresh.append(test_id)
    return fresh


def gate(repo: Path, base: str, audit: bool = False, pytests: bool = True) -> dict:
    started = time.time()
    head = git(repo, "rev-parse", "HEAD").strip()
    rows = changed_files(repo, base)
    paths = [path for _, path in rows]
    live = [path for status, path in rows if status != "D"]
    report: dict = {
        "base": base,
        "head": head,
        "changed": paths,
        "reasons": [],
        "preexisting": [],
    }

    bad = forbidden(paths)
    if audit:
        # Whole-branch audits include the old factory tooling commits, which
        # are hidden from review and never offered for master.
        bad = [p for p in bad if not p.startswith(C.TOOLING_PREFIXES)]
        report["audit"] = True
    if bad:
        report["reasons"].append("Changed files the factory may not touch: " + ", ".join(bad))
    if not audit:
        production, total = diff_size(repo, base)
        report["lines"] = {"production": production, "total": total}
        if production > MAX_PRODUCTION_LINES or total > MAX_TOTAL_LINES:
            report["reasons"].append(
                f"Too big to review: {production} production lines and {total} total lines changed "
                f"(limits {MAX_PRODUCTION_LINES} and {MAX_TOTAL_LINES}). Make a smaller change."
            )
    grown = baseline_growth(repo, base)
    if grown:
        report["reasons"].append("Added source-text BASELINE entries: " + ", ".join(grown))

    py = [p for p in live if p.endswith(".py")]
    if py:
        compiled = run(["uv", "run", "python", "-m", "py_compile", *py], repo, 300)
        if compiled.returncode != 0:
            report["reasons"].append("Python does not compile: " + compiled.stderr.strip()[-600:])
    for rel in (p for p in live if p.endswith(".js") and not SKIP_NODE_CHECK.search(p)):
        checked = run(["node", "--check", rel], repo, 60)
        if checked.returncode != 0:
            report["reasons"].append(f"JavaScript does not parse: {rel}")

    node_files = node_test_files(repo, paths)
    node_failed, node_tail = failing_node_files(repo, node_files)
    node_new = new_node_failures(repo, base, node_failed)
    if node_new:
        report["reasons"].append("New node test failures: " + ", ".join(node_new))
        report["node_output"] = node_tail
    report["preexisting"] += [f for f in node_failed if f not in node_new]

    py_files = related_pytests(repo, paths) if pytests else []
    py_failed, py_tail = failing_pytests(repo, py_files)
    py_new = new_pytest_failures(repo, base, py_failed)
    if py_new:
        report["reasons"].append("New pytest failures: " + ", ".join(py_new[:12]))
        report["pytest_output"] = py_tail
    report["preexisting"] += [i for i in py_failed if i not in py_new]

    report["node_files"] = len(node_files)
    report["pytest_files"] = py_files
    report["passed"] = not report["reasons"]
    report["seconds"] = round(time.time() - started, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--repo", default=str(C.WORKTREE))
    parser.add_argument("--out")
    parser.add_argument("--audit", action="store_true", help="whole-branch check; ignore old tooling paths")
    parser.add_argument("--no-pytest", action="store_true", help="skip pytest (the caller runs the full suite)")
    args = parser.parse_args()
    report = gate(Path(args.repo), args.base, audit=args.audit, pytests=not args.no_pytest)
    text = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    verdict = "PASSED" if report["passed"] else "FAILED"
    print(f"gate {verdict} in {report['seconds']}s: {len(report['changed'])} files changed, "
          f"{report['node_files']} node test files, {len(report['pytest_files'])} pytest files")
    for reason in report["reasons"]:
        print("  - " + reason)
    if report["preexisting"]:
        print("  (already failing before this change: " + ", ".join(report["preexisting"][:8]) + ")")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
