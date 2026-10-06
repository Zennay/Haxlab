from __future__ import annotations

import hashlib
import os
import tempfile
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


def _source_signature(stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _copy_to_staging_and_hash(
    source_path: Path,
    raw_root: Path,
) -> tuple[Path, str, int]:
    """Copy one source snapshot to private staging while hashing those exact bytes."""

    staging_root = raw_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    staging_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="ingest-",
            suffix=".hbr2.tmp",
            dir=staging_root,
            delete=False,
        ) as target:
            staging_path = Path(target.name)
            digest = hashlib.sha256()
            copied_bytes = 0

            with source_path.open("rb") as source:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                    digest.update(chunk)
                    copied_bytes += len(chunk)

            target.flush()
            os.fsync(target.fileno())

        return staging_path, digest.hexdigest(), copied_bytes
    except Exception:
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)
        raise


def archive_replay(
    source_path: Path,
    raw_root: Path,
    state: RuntimeState,
) -> ArchiveResult:
    """Archive a settled replay without trusting a source that changes mid-copy."""

    source_before = source_path.stat()
    staging_path, sha256, copied_bytes = _copy_to_staging_and_hash(
        source_path,
        raw_root,
    )

    try:
        source_after = source_path.stat()
        if (
            copied_bytes != source_before.st_size
            or _source_signature(source_before) != _source_signature(source_after)
        ):
            raise RuntimeError("source_changed_during_archive")

        validation = validate_replay_basic(staging_path)
        if not validation.valid:
            raise ValueError("invalid_hbr2:" + ",".join(validation.reasons))

        destination = archive_path_for(raw_root, sha256)
        duplicate = state.raw_exists(sha256)

        if duplicate:
            if not destination.is_file():
                raise RuntimeError(f"raw_archive_missing:{sha256}")
            if sha256_file(destination) != sha256:
                raise RuntimeError(f"raw_archive_hash_mismatch:{sha256}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)

            if destination.exists():
                # A previous process may have atomically finalized the object and
                # crashed before registering it in SQLite. Recover only when the
                # on-disk object proves it matches its content-addressed path.
                if not destination.is_file() or sha256_file(destination) != sha256:
                    raise RuntimeError(f"raw_archive_hash_mismatch:{sha256}")
            else:
                staging_path.replace(destination)

            state.register_raw(
                sha256=sha256,
                archive_path=str(destination),
                size_bytes=copied_bytes,
            )

        return ArchiveResult(
            source_path=source_path,
            sha256=sha256,
            archive_path=destination,
            duplicate=duplicate,
        )
    finally:
        staging_path.unlink(missing_ok=True)
