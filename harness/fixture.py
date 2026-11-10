#!/usr/bin/env python3
"""Produce a sanitized copy of bots_config.json for factory worktrees.

Structure and every non-secret setting are preserved verbatim; every
*string* value whose key name looks secret-bearing is replaced with a
placeholder.  Empty strings stay empty (code paths often branch on
``if not token``), and non-string values (bools/ints/lists) are untouched.
Keys that are clearly filesystem *paths to* secrets (``*_file``, ``*_path``,
``*_dir``) are kept — they are not secrets themselves and the referenced
files are absent in a worktree anyway.

Usage:
    sanitize_bots_config.py SRC DEST
"""

from __future__ import annotations

import json
import re
import sys

PLACEHOLDER = "test-placeholder"

SECRET_KEY = re.compile(
    r"(token|api_key|apikey|secret|password|passwd|webhook|cookie|credential|"
    r"access_key|private|vault_key|client_id|key_id|team_id|email|^key$|_key$)",
    re.I,
)
PATH_KEY = re.compile(r"(_file|_path|_dir)$", re.I)


def _is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY.search(key)) and not PATH_KEY.search(key)


def sanitize(node, key: str | None = None):
    if isinstance(node, dict):
        return {k: sanitize(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [sanitize(v, key) for v in node]
    if isinstance(node, str) and key is not None and _is_secret_key(key):
        return node if node == "" else PLACEHOLDER
    return node


REAL = "/srv/factory/target/bots_config.json"
# llm.py probes the real gateway to size tool budgets; with a placeholder key
# the probe fails and seven tests change behaviour. Pin the budget instead.
ENV = {"VLLM_MAX_MODEL_LEN": "24576"}


def install(checkout) -> bool:
    """Write the sanitized config into a factory checkout; never copies secrets."""
    try:
        with open(REAL) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    dest = str(checkout) + "/bots_config.json"
    with open(dest, "w") as f:
        json.dump(sanitize(data), f, indent=2)
        f.write("\n")
    return True


def main(argv: list[str]) -> int:
    if len(argv) == 2:
        return 0 if install(argv[1]) else 1
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    src, dest = argv[1], argv[2]
    with open(src) as f:
        data = json.load(f)
    out = sanitize(data)
    with open(dest, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    redacted = sorted(_redacted_paths(data, out))
    print(f"wrote {dest}; redacted {len(redacted)} values:")
    for p in redacted:
        print("  " + p)
    return 0


def _redacted_paths(a, b, prefix: str = ""):
    if isinstance(a, dict):
        for k in a:
            yield from _redacted_paths(a[k], b[k], f"{prefix}.{k}" if prefix else k)
    elif isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            yield from _redacted_paths(x, y, f"{prefix}[{i}]")
    elif a != b:
        yield prefix


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
