"""Where the factory lives and what it works on.

The factory code is deliberately outside the repository the agent edits,
so a pass cannot change its own prompt, gate, or permissions.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

HOME = Path(__file__).resolve().parent
STATE = Path(os.environ.get("FACTORY_STATE", HOME / "state"))

WORKTREE = Path(os.environ.get("FACTORY_WORKTREE", "/srv/factory/target-lane"))
BRANCH = os.environ.get("FACTORY_BRANCH", "grok/improvements")
MASTER = "master"

LOG = STATE / "loop.log"
PIDFILE = STATE / "loop.pid"
STOPFILE = STATE / "stop"
MODELFILE = STATE / "model"
SKIPFILE = STATE / "skipped.txt"
INCLUDED = STATE / "included.jsonl"
VERDICTS = STATE / "verdicts.jsonl"
GATE_DIR = STATE / "gate"
AUDIT = STATE / "audit.json"

DEFAULT_MODEL = os.environ.get("FACTORY_MODEL", "fracktron/grok-4.7")
MODEL_CHOICES = (
    "fracktron/grok-4.7",
    "fracktron/grok-4.7-api",
    "xai/grok-4.7",
)

REVIEWFILE = STATE / "inline_review"
REVIEW_MODELFILE = STATE / "review_model"
DEFAULT_REVIEWER = "fracktron/claude-opus-5-5"
REVIEWER_CHOICES = (
    "fracktron/claude-opus-5-5",
    "fracktron/claude-sonnet-5-5",
    "fracktron/gpt-6.1-sol",
)
INTEGRATION = STATE / "integration.json"

USAGE_DB = Path("/srv/factory/operator/.local/share/opencode/opencode.db")
SCRATCH = Path(os.environ.get("FACTORY_SCRATCH", "/srv/factory/scratch/base-check"))
AUDIT_SCRATCH = Path("/srv/factory/scratch/audit-check")
AUDIT_PID = STATE / "audit.pid"

# The first loop wrote here. Read it until the new loop has taken over.
OLD_LOG = Path("/srv/factory/scratch/grok-improve-loop.log")
OLD_PID = Path("/srv/factory/scratch/grok-improve-loop.pid")
OLD_SKIPFILE = Path("/srv/factory/scratch/grok-factory-skipped.txt")
LOOP_UNIT = "grok-factory-loop.service"

# Commits that only touch these paths are factory tooling from before the
# factory moved out of the repo. They are not offered for master.
TOOLING_PREFIXES = (
    "scripts/grok_",
    "tests/test_grok_factory_status.py",
    "GROK_REPORT.md",
)

LANES_DIR = HOME / "lanes"


@dataclass(frozen=True)
class Lane:
    """One parallel factory loop: its own worktree, branch, and state."""

    id: str
    worktree: Path
    branch: str
    state: Path
    title: str
    focus: Path
    frozen: bool = False

    @property
    def log(self) -> Path:
        return self.state / "loop.log"

    @property
    def pidfile(self) -> Path:
        return self.state / "loop.pid"

    @property
    def stopfile(self) -> Path:
        return self.state / "stop"

    @property
    def audit(self) -> Path:
        return self.state / "audit.json"

    @property
    def audit_pid(self) -> Path:
        return self.state / "audit.pid"

    @property
    def unit(self) -> str:
        return f"grok-factory-lane@{self.id}.service"

    @property
    def name(self) -> str:
        try:
            for line in self.focus.read_text().splitlines():
                if line.startswith("## Your lane:"):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
        return self.id


def _read_env(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def lanes(directory: Path | None = None) -> list[Lane]:
    out = []
    def _order(path: Path) -> tuple[int, str]:
        digits = "".join(ch for ch in path.stem if ch.isdigit())
        return (int(digits) if digits else 0, path.stem)

    for path in sorted((directory or LANES_DIR).glob("*.env"), key=_order):
        env = _read_env(path)
        out.append(Lane(
            id=env.get("FACTORY_LANE", path.stem),
            worktree=Path(env["FACTORY_WORKTREE"]),
            branch=env["FACTORY_BRANCH"],
            state=Path(env["FACTORY_STATE"]),
            title=env.get("FACTORY_TITLE", path.stem + "-improve"),
            focus=Path(env.get("FACTORY_FOCUS", str(HOME / "focus" / f"{path.stem}.md"))),
            frozen=env.get("FACTORY_FROZEN", "") == "1",
        ))
    return out


def lane(lane_id: str) -> Lane | None:
    return next((item for item in lanes() if item.id == lane_id), None)


# A pass may not change these. The gate rejects the pass if it does.
FORBIDDEN_PATTERNS = (
    r"(^|/)bots_config\.json$",
    r"(^|/)android-release\.json$",
    r"(^|/)opencode\.jsonc?$",
    r"^\.opencode/",
    r"^runtime/",
    r"^logs/",
    r"^scripts/ship[^/]*\.py$",
    r"^studio_dev_deploy\.py$",
    r"^scripts/grok_",
    r"(^|/)\.env(\..*)?$",
    r"^GROK_REPORT\.md$",
)
