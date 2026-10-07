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


def _secure_archive_snapshot(path: Path) -> tuple[int, str]:
    """Read one stable regular-file inode and hash exactly those descriptor bytes."""

    try:
        initial = path.lstat()
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
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise _ArchiveReadError("archive_missing")
    except OSError:
        _raise_open_failure(path)

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
            final = path.lstat()
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

        return total, digest.hexdigest()
    except OSError:
        raise _ArchiveReadError("archive_read_failed")
    finally:
        os.close(fd)


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
        sha256 = str(row["sha256"])
        archive_path = str(row["archive_path"])
        expected_size = int(row["size_bytes"])
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
