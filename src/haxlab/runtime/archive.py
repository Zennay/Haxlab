from __future__ import annotations

import hashlib
import os
import stat
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


def _source_signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_verified_source(source_path: Path) -> tuple[int, os.stat_result]:
    try:
        initial = source_path.lstat()
    except OSError as exc:
        raise RuntimeError("source_changed_during_archive") from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise RuntimeError("source_changed_during_archive")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise RuntimeError("source_changed_during_archive")
    flags = os.O_RDONLY | nofollow | nonblock
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(source_path, flags)
    except OSError as exc:
        raise RuntimeError("source_changed_during_archive") from exc

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise RuntimeError("source_changed_during_archive")
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise RuntimeError("source_changed_during_archive")
        return fd, opened
    except Exception:
        os.close(fd)
        raise


def _copy_to_staging_and_hash(
    source_path: Path,
    raw_root: Path,
) -> tuple[Path, str, int]:
    """Copy one verified source inode to private staging while hashing exact bytes."""

    staging_root = raw_root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)

    staging_path: Path | None = None
    source_fd: int | None = None
    try:
        source_fd, source_before = _open_verified_source(source_path)

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

            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)
                digest.update(chunk)
                copied_bytes += len(chunk)

            source_after = os.fstat(source_fd)
            if (
                copied_bytes != source_after.st_size
                or _source_signature(source_before) != _source_signature(source_after)
            ):
                raise RuntimeError("source_changed_during_archive")

            try:
                final = source_path.lstat()
            except OSError as exc:
                raise RuntimeError("source_changed_during_archive") from exc
            if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
                raise RuntimeError("source_changed_during_archive")
            if _source_signature(final) != _source_signature(source_after):
                raise RuntimeError("source_changed_during_archive")

            target.flush()
            os.fsync(target.fileno())

        return staging_path, digest.hexdigest(), copied_bytes
    except Exception:
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)
        raise
    finally:
        if source_fd is not None:
            os.close(source_fd)


def archive_replay(
    source_path: Path,
    raw_root: Path,
    state: RuntimeState,
) -> ArchiveResult:
    """Archive a settled replay without trusting a source that changes mid-copy."""

    staging_path, sha256, copied_bytes = _copy_to_staging_and_hash(
        source_path,
        raw_root,
    )

    try:
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
