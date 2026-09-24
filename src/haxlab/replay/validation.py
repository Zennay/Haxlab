from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from haxlab.replay.header import ReplayFormatError, read_replay_header


@dataclass(frozen=True)
class ReplayValidation:
    valid: bool
    reasons: tuple[str, ...] = ()
    version: int | None = None
    total_frames: int | None = None
    duration_seconds: float | None = None


def validate_replay_basic(path: Path) -> ReplayValidation:
    """Cheap HBR2 v3 header validation before deeper parsing/decompression."""
    try:
        header = read_replay_header(path)
    except OSError as exc:
        return ReplayValidation(False, (f"read_failed:{exc}",))
    except ReplayFormatError as exc:
        return ReplayValidation(False, (str(exc),))

    if header.total_frames <= 0:
        return ReplayValidation(
            False,
            ("invalid_total_frames",),
            version=header.version,
            total_frames=header.total_frames,
            duration_seconds=header.duration_seconds,
        )

    return ReplayValidation(
        True,
        (),
        version=header.version,
        total_frames=header.total_frames,
        duration_seconds=header.duration_seconds,
    )
