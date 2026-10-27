"""Run the whole Python test suite quickly by splitting files across processes.

pytest-xdist cannot be used here: some tests build parametrize ids from
unordered data, so workers collect different test lists. Separate pytest
processes over disjoint file groups do not have that problem.
"""

from __future__ import annotations

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FAIL = re.compile(r"^(FAILED|ERROR) (\S+)")
GLOBS = ("tests/test_*.py", "public_api/tests/test_*.py")


def test_files(repo: Path) -> list[str]:
    files: list[str] = []
    for pattern in GLOBS:
        files += sorted(str(p.relative_to(repo)) for p in repo.glob(pattern))
    return files


def _run(repo: Path, files: list[str], timeout: int) -> tuple[int, list[str], str]:
    if not files:
        return 0, [], ""
    try:
        proc = subprocess.run(
            ["uv", "run", "pytest", "-q", "-rfE", "-p", "no:cacheprovider", *files],
            cwd=repo, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, [f"<timed out: {files[0]} and {len(files) - 1} more>"], ""
    ids = [m.group(2) for line in proc.stdout.splitlines() if (m := FAIL.match(line.strip()))]
    if proc.returncode not in (0, 1, 5) and not ids:
        ids = [f"<pytest exit {proc.returncode}: {files[0]} and {len(files) - 1} more>"]
    return proc.returncode, ids, proc.stdout[-2000:]


def run(repo: Path, workers: int = 12, timeout: int = 2400) -> tuple[list[str], int]:
    """(failing test ids, number of files). Files are dealt round-robin to workers."""
    files = test_files(repo)
    groups = [files[i::workers] for i in range(workers)]
    subprocess.run(["uv", "sync", "-q"], cwd=repo, capture_output=True, check=False)
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for _, ids, _ in pool.map(lambda g: _run(repo, g, timeout), groups):
            failed += ids
    return failed, len(files)


if __name__ == "__main__":
    import sys
    import time

    started = time.time()
    failed, count = run(Path(sys.argv[1]))
    print(f"{count} files, {len(failed)} failing, {time.time() - started:.0f}s")
    for item in failed[:40]:
        print("  " + item)
