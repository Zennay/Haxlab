from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

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


def _verify_existing_archive(
    destination: Path,
    sha256: str,
    *,
    missing_error: str,
) -> None:
    mismatch_error = f"raw_archive_hash_mismatch:{sha256}"
    try:
        initial = destination.lstat()
    except FileNotFoundError as exc:
        raise RuntimeError(missing_error) from exc
    except OSError as exc:
        raise RuntimeError(mismatch_error) from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise RuntimeError(mismatch_error)

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise RuntimeError(mismatch_error)
    flags = os.O_RDONLY | nofollow | nonblock
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(destination, flags)
    except FileNotFoundError as exc:
        raise RuntimeError(missing_error) from exc
    except OSError as exc:
        raise RuntimeError(mismatch_error) from exc

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(mismatch_error)
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            raise RuntimeError(mismatch_error)

        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)

        after = os.fstat(fd)
        if (
            total != after.st_size
            or _source_signature(before) != _source_signature(after)
        ):
            raise RuntimeError(mismatch_error)

        try:
            final = destination.lstat()
        except FileNotFoundError as exc:
            raise RuntimeError(missing_error) from exc
        except OSError as exc:
            raise RuntimeError(mismatch_error) from exc
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            raise RuntimeError(mismatch_error)
        if _source_signature(final) != _source_signature(after):
            raise RuntimeError(mismatch_error)
        if digest.hexdigest() != sha256:
            raise RuntimeError(mismatch_error)
    finally:
        os.close(fd)


def _publish_archive_no_clobber(
    staging_path: Path,
    destination: Path,
    sha256: str,
) -> None:
    mismatch_error = f"raw_archive_hash_mismatch:{sha256}"
    try:
        os.link(staging_path, destination, follow_symlinks=False)
    except FileExistsError:
        _verify_existing_archive(
            destination,
            sha256,
            missing_error=mismatch_error,
        )
        return
    except OSError as exc:
        raise RuntimeError(f"raw_archive_publish_failed:{sha256}") from exc

    _verify_existing_archive(
        destination,
        sha256,
        missing_error=mismatch_error,
    )


def archive_replay(
    source_path: Path,
    raw_root: Path,
    state: RuntimeState,
) -> ArchiveResult:
    """Archive a settled replay without trusting mutable source/destination paths."""

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
            _verify_existing_archive(
                destination,
                sha256,
                missing_error=f"raw_archive_missing:{sha256}",
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            _publish_archive_no_clobber(staging_path, destination, sha256)
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
