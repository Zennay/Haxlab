from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat

from haxlab.runtime.state import RuntimeState


AUDIT_SCHEMA = "haxlab-raw-archive-audit-v1"
_READ_CHUNK_BYTES = 1024 * 1024


class _ArchiveReadError(RuntimeError):
    """Deterministic fail-closed reason for unsafe archive evidence."""


def _content_address_path_matches(path: Path, sha256: str) -> bool:
    return (
        path.name == f"{sha256}.hbr2"
        and path.parent.name == sha256[2:4]
        and path.parent.parent.name == sha256[:2]
    )


def _validated_archive_ledger_row(row) -> tuple[str, str, int]:
    """Validate persisted raw_replays evidence before filesystem trust."""

    sha256 = row["sha256"]
    if (
        type(sha256) is not str
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise _ArchiveReadError("invalid_ledger_evidence:sha256")

    archive_path = row["archive_path"]
    if type(archive_path) is not str or archive_path == "":
        raise _ArchiveReadError("invalid_ledger_evidence:archive_path")

    size_bytes = row["size_bytes"]
    if type(size_bytes) is not int or size_bytes < 0:
        raise _ArchiveReadError("invalid_ledger_evidence:size_bytes")

    return sha256, archive_path, size_bytes


def _raise_open_failure(path: Path) -> None:
    """Classify the path after a failed no-follow open without trusting it."""

    try:
        current = path.lstat()
    except FileNotFoundError:
        raise _ArchiveReadError("archive_missing")
    except OSError:
        raise _ArchiveReadError("archive_secure_open_failed")
    if stat.S_ISLNK(current.st_mode):
        raise _ArchiveReadError("symlink_not_allowed")
    raise _ArchiveReadError("archive_secure_open_failed")


def _raise_member_open_failure(parent_fd: int, name: str) -> None:
    """Classify one descriptor-relative member after a failed no-follow open."""

    try:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise _ArchiveReadError("archive_missing")
    except OSError:
        raise _ArchiveReadError("archive_secure_open_failed")
    if stat.S_ISLNK(current.st_mode):
        raise _ArchiveReadError("symlink_not_allowed")
    raise _ArchiveReadError("archive_secure_open_failed")


def _open_bound_parent(path: Path) -> tuple[int, os.stat_result]:
    """Bind the logical archive parent to one no-follow directory inode."""

    try:
        initial = path.lstat()
    except FileNotFoundError:
        raise _ArchiveReadError("archive_missing")
    except OSError:
        raise _ArchiveReadError("archive_secure_open_failed")

    if stat.S_ISLNK(initial.st_mode):
        raise _ArchiveReadError("symlink_not_allowed")
    if not stat.S_ISDIR(initial.st_mode):
        raise _ArchiveReadError("archive_missing")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if nofollow is None or directory is None:
        raise _ArchiveReadError("archive_secure_open_unsupported")

    flags = os.O_RDONLY | nofollow | directory
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise _ArchiveReadError("archive_missing")
    except OSError:
        _raise_open_failure(path)

    try:
        opened = os.fstat(fd)
        if not stat.S_ISDIR(opened.st_mode):
            raise _ArchiveReadError("archive_missing")
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            raise _ArchiveReadError("archive_parent_identity_changed")
        return fd, opened
    except Exception:
        os.close(fd)
        raise


def _assert_logical_parent_identity(path: Path, expected: os.stat_result) -> None:
    """Prove the logical parent still resolves to the descriptor-bound directory."""

    try:
        current = path.lstat()
    except OSError:
        raise _ArchiveReadError("archive_parent_changed_during_read")
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
        raise _ArchiveReadError("archive_parent_changed_during_read")
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise _ArchiveReadError("archive_parent_changed_during_read")


def _secure_archive_snapshot(path: Path) -> tuple[int, str]:
    """Read one stable member from one stable content-address directory inode."""

    parent_fd: int | None = None
    try:
        parent_fd, parent_identity = _open_bound_parent(path.parent)

        try:
            initial = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            raise _ArchiveReadError("archive_missing")
        except OSError:
            raise _ArchiveReadError("archive_read_failed")

        if stat.S_ISLNK(initial.st_mode):
            raise _ArchiveReadError("symlink_not_allowed")
        if not stat.S_ISREG(initial.st_mode):
            # Preserve the previous audit semantics: non-files count as missing archive
            # evidence rather than as successfully opened objects.
            raise _ArchiveReadError("archive_missing")

        nofollow = getattr(os, "O_NOFOLLOW", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if nofollow is None or nonblock is None:
            raise _ArchiveReadError("archive_secure_open_unsupported")

        flags = os.O_RDONLY | nofollow | nonblock
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC

        try:
            fd = os.open(path.name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            raise _ArchiveReadError("archive_missing")
        except OSError:
            _raise_member_open_failure(parent_fd, path.name)

        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise _ArchiveReadError("archive_not_regular")
            if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
                raise _ArchiveReadError("archive_identity_changed")

            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = os.read(fd, _READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)

            after = os.fstat(fd)
            before_identity = (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            )
            after_identity = (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            )
            if after_identity != before_identity or total != after.st_size:
                raise _ArchiveReadError("archive_changed_during_read")

            try:
                final = os.stat(
                    path.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError:
                raise _ArchiveReadError("archive_path_changed_during_read")
            if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
                raise _ArchiveReadError("archive_path_changed_during_read")
            final_identity = (
                final.st_dev,
                final.st_ino,
                final.st_size,
                final.st_mtime_ns,
                final.st_ctime_ns,
            )
            if final_identity != after_identity:
                raise _ArchiveReadError("archive_path_changed_during_read")

            _assert_logical_parent_identity(path.parent, parent_identity)
            return total, digest.hexdigest()
        except OSError:
            raise _ArchiveReadError("archive_read_failed")
        finally:
            os.close(fd)
    finally:
        if parent_fd is not None:
            os.close(parent_fd)


def audit_raw_archive(
    state: RuntimeState,
    *,
    max_issues: int = 100,
) -> dict[str, object]:
    """Verify every raw_replays ledger row against immutable on-disk evidence."""

    rows = state.connection.execute(
        """
        SELECT sha256, archive_path, size_bytes
        FROM raw_replays
        ORDER BY sha256
        """
    ).fetchall()

    checked_records = 0
    existing_files = 0
    missing_files = 0
    size_mismatches = 0
    hash_mismatches = 0
    path_mismatches = 0
    symlink_entries = 0
    read_failures = 0
    objects_with_issues = 0
    issues: list[dict[str, object]] = []

    for row in rows:
        checked_records += 1
        raw_sha256 = row["sha256"]
        raw_archive_path = row["archive_path"]
        try:
            sha256, archive_path, expected_size = _validated_archive_ledger_row(row)
        except _ArchiveReadError as exc:
            objects_with_issues += 1
            read_failures += 1
            if len(issues) < max(0, max_issues):
                issues.append(
                    {
                        "sha256": raw_sha256,
                        "archive_path": raw_archive_path,
                        "reasons": [str(exc)],
                    }
                )
            continue

        path = Path(archive_path)
        reasons: list[str] = []

        if not _content_address_path_matches(path, sha256):
            path_mismatches += 1
            reasons.append("content_address_path_mismatch")

        try:
            actual_size, actual_sha256 = _secure_archive_snapshot(path)
        except _ArchiveReadError as exc:
            reason = str(exc)
            if reason == "archive_missing":
                missing_files += 1
            elif reason == "symlink_not_allowed":
                symlink_entries += 1
            else:
                read_failures += 1
            reasons.append(reason)
        else:
            existing_files += 1
            if actual_size != expected_size:
                size_mismatches += 1
                reasons.append(
                    f"size_mismatch:expected={expected_size}:actual={actual_size}"
                )
            if actual_sha256 != sha256:
                hash_mismatches += 1
                reasons.append(
                    f"sha256_mismatch:expected={sha256}:actual={actual_sha256}"
                )

        if reasons:
            objects_with_issues += 1
            if len(issues) < max(0, max_issues):
                issues.append(
                    {
                        "sha256": sha256,
                        "archive_path": archive_path,
                        "reasons": reasons,
                    }
                )

    return {
        "schema": AUDIT_SCHEMA,
        "ok": objects_with_issues == 0,
        "checked_records": checked_records,
        "existing_files": existing_files,
        "missing_files": missing_files,
        "size_mismatches": size_mismatches,
        "hash_mismatches": hash_mismatches,
        "path_mismatches": path_mismatches,
        "symlink_entries": symlink_entries,
        "read_failures": read_failures,
        "objects_with_issues": objects_with_issues,
        "issues": issues,
        "issues_truncated": objects_with_issues > len(issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-audit-archive")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument("--max-issues", type=int, default=100)
    args = parser.parse_args()

    with RuntimeState(args.state_db) as state:
        report = audit_raw_archive(state, max_issues=args.max_issues)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["ok"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
