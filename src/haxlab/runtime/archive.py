from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from haxlab.hashing import sha256_file
from haxlab.replay.validation import validate_replay_basic
from haxlab.runtime.state import RuntimeState


@dataclass(frozen=True)
class ArchiveResult:
    source_path: Path
    sha256: str
    archive_path: Path
    duplicate: bool


def archive_path_for(raw_root: Path, sha256: str) -> Path:
    return raw_root / sha256[:2] / sha256[2:4] / f"{sha256}.hbr2"


def archive_replay(
    source_path: Path,
    raw_root: Path,
    state: RuntimeState,
) -> ArchiveResult:
    validation = validate_replay_basic(source_path)
    if not validation.valid:
        raise ValueError("invalid_hbr2:" + ",".join(validation.reasons))

    sha256 = sha256_file(source_path)
    destination = archive_path_for(raw_root, sha256)
    duplicate = state.raw_exists(sha256)

    if not duplicate:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".hbr2.tmp")

        with source_path.open("rb") as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())

        # Atomic finalization. A crash before this point only leaves a .tmp file.
        temporary.replace(destination)

        state.register_raw(
            sha256=sha256,
            archive_path=str(destination),
            size_bytes=source_path.stat().st_size,
        )

    return ArchiveResult(
        source_path=source_path,
        sha256=sha256,
        archive_path=destination,
        duplicate=duplicate,
    )
