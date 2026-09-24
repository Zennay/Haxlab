from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ReplayValidation:
    valid: bool
    reasons: tuple[str, ...] = ()


def validate_replay_basic(path: Path) -> ReplayValidation:
    """Cheap integrity check before deeper HBR2 parsing."""
    reasons: list[str] = []

    try:
        size = path.stat().st_size
    except OSError as exc:
        return ReplayValidation(False, (f"stat_failed:{exc}",))

    if size < 8:
        reasons.append("file_too_small")

    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError as exc:
        return ReplayValidation(False, (f"read_failed:{exc}",))

    if magic != b"HBR2":
        reasons.append("invalid_magic")

    return ReplayValidation(not reasons, tuple(reasons))
