from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


RECEIPT_SCHEMA = "haxlab-runtime-state-backup-v1"


class StateBackupError(RuntimeError):
    """Raised when a runtime-state backup cannot be proven safe."""


@dataclass(frozen=True)
class BackupReceipt:
    schema: str
    size_bytes: int
    sha256: str
    page_count: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path, *, include_leaf: bool) -> None:
    current = path if include_leaf else path.parent
    while True:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise StateBackupError(f"path component does not exist: {current}") from exc
        except OSError as exc:
            raise StateBackupError(
                f"unable to inspect path component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise StateBackupError(f"symlink path component is not allowed: {current}")
        if current == current.parent:
            return
        current = current.parent


def _regular_file_identity(path: Path, *, label: str) -> tuple[int, int]:
    _reject_symlink_components(path, include_leaf=True)
    try:
        file_stat = path.lstat()
    except FileNotFoundError as exc:
        raise StateBackupError(f"{label} does not exist: {path}") from exc
    except OSError as exc:
        raise StateBackupError(f"unable to inspect {label} {path}: {exc}") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise StateBackupError(f"{label} is not a regular file: {path}")
    return file_stat.st_dev, file_stat.st_ino


def _source_identity(path: Path) -> tuple[int, int]:
    return _regular_file_identity(path, label="source database")


def _snapshot_identity(path: Path) -> tuple[int, int]:
    return _regular_file_identity(path, label="backup snapshot")


def _require_snapshot_read_only(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise StateBackupError(
            f"unable to inspect backup snapshot permissions {path}: {exc}"
        ) from exc
    if not stat.S_ISREG(mode):
        raise StateBackupError(f"backup snapshot is not a regular file: {path}")
    if stat.S_IMODE(mode) & 0o222:
        raise StateBackupError("backup snapshot must be sealed read-only")


def _path_identity(path: Path) -> tuple[int, int]:
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise StateBackupError(f"unable to inspect path identity {path}: {exc}") from exc
    return file_stat.st_dev, file_stat.st_ino


def _validate_destination(source: Path, destination: Path) -> None:
    if source == destination:
        raise StateBackupError("source and destination must be different paths")

    _reject_symlink_components(destination, include_leaf=False)
    try:
        parent_stat = destination.parent.lstat()
    except FileNotFoundError as exc:
        raise StateBackupError(
            f"destination parent does not exist: {destination.parent}"
        ) from exc
    except OSError as exc:
        raise StateBackupError(
            f"unable to inspect destination parent {destination.parent}: {exc}"
        ) from exc
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise StateBackupError(
            f"destination parent is not a directory: {destination.parent}"
        )

    if os.path.lexists(destination):
        raise StateBackupError(f"destination already exists: {destination}")


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise StateBackupError(f"unable to open source database read-only: {exc}") from exc


def _connect_immutable(path: Path) -> sqlite3.Connection:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=5.0,
        )
        connection.execute("PRAGMA query_only=ON")
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise StateBackupError(
            f"unable to open backup snapshot immutable/read-only: {exc}"
        ) from exc


def _quick_check(connection: sqlite3.Connection, *, label: str) -> None:
    try:
        rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    except sqlite3.Error as exc:
        raise StateBackupError(f"{label} quick_check failed to execute: {exc}") from exc
    if rows != ["ok"]:
        detail = "; ".join(rows) if rows else "no result"
        raise StateBackupError(f"{label} quick_check failed: {detail}")


def _page_count(connection: sqlite3.Connection) -> int:
    try:
        value = connection.execute("PRAGMA page_count").fetchone()
    except sqlite3.Error as exc:
        raise StateBackupError(f"unable to read snapshot page count: {exc}") from exc
    if value is None or type(value[0]) is not int or value[0] < 1:
        raise StateBackupError("snapshot page count is invalid")
    return value[0]


def _hash_file(
    path: Path,
    *,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, str]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StateBackupError(f"unable to open snapshot for hashing: {exc}") from exc

    digest = hashlib.sha256()
    size = 0
    try:
        try:
            first_stat = os.fstat(fd)
        except OSError as exc:
            raise StateBackupError(
                f"unable to inspect snapshot while hashing: {exc}"
            ) from exc
        if not stat.S_ISREG(first_stat.st_mode):
            raise StateBackupError("snapshot hash input is not a regular file")
        first_identity = (first_stat.st_dev, first_stat.st_ino)
        if expected_identity is not None and first_identity != expected_identity:
            raise StateBackupError("snapshot identity changed before hashing")

        while True:
            try:
                chunk = os.read(fd, 1024 * 1024)
            except OSError as exc:
                raise StateBackupError(f"unable to hash snapshot: {exc}") from exc
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)

        try:
            final_stat = os.fstat(fd)
        except OSError as exc:
            raise StateBackupError(
                f"unable to re-inspect snapshot after hashing: {exc}"
            ) from exc
        final_identity = (final_stat.st_dev, final_stat.st_ino)
        if final_identity != first_identity:
            raise StateBackupError("snapshot identity changed during hashing")
        if expected_identity is not None and final_identity != expected_identity:
            raise StateBackupError("snapshot identity changed during hashing")
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if size <= 0:
        raise StateBackupError("snapshot is empty")
    return size, digest.hexdigest()


def _validate_receipt(receipt: BackupReceipt) -> None:
    if type(receipt) is not BackupReceipt:
        raise StateBackupError("backup receipt must be a BackupReceipt")
    if receipt.schema != RECEIPT_SCHEMA:
        raise StateBackupError(f"unsupported backup receipt schema: {receipt.schema!r}")
    if type(receipt.size_bytes) is not int or receipt.size_bytes <= 0:
        raise StateBackupError("backup receipt size_bytes must be a positive native integer")
    if type(receipt.page_count) is not int or receipt.page_count <= 0:
        raise StateBackupError("backup receipt page_count must be a positive native integer")
    if (
        type(receipt.sha256) is not str
        or len(receipt.sha256) != 64
        or any(character not in "0123456789abcdef" for character in receipt.sha256)
    ):
        raise StateBackupError("backup receipt sha256 must be lowercase 64-char hex")


def parse_backup_receipt(payload: object) -> BackupReceipt:
    if type(payload) is not dict:
        raise StateBackupError("backup receipt payload must be a JSON object")
    expected_keys = {"schema", "size_bytes", "sha256", "page_count"}
    actual_keys = set(payload)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        extra = sorted(actual_keys - expected_keys)
        raise StateBackupError(
            f"backup receipt fields are not canonical: missing={missing}, extra={extra}"
        )

    receipt = BackupReceipt(
        schema=payload["schema"],
        size_bytes=payload["size_bytes"],
        sha256=payload["sha256"],
        page_count=payload["page_count"],
    )
    _validate_receipt(receipt)
    return receipt


def verify_runtime_state_backup(
    snapshot: Path,
    receipt: BackupReceipt,
) -> BackupReceipt:
    _validate_receipt(receipt)
    snapshot = _absolute(snapshot)
    snapshot_identity = _snapshot_identity(snapshot)
    _require_snapshot_read_only(snapshot)

    first_size, first_sha256 = _hash_file(
        snapshot,
        expected_identity=snapshot_identity,
    )
    if first_size != receipt.size_bytes:
        raise StateBackupError(
            f"backup snapshot size does not match receipt: "
            f"{first_size} != {receipt.size_bytes}"
        )
    if first_sha256 != receipt.sha256:
        raise StateBackupError("backup snapshot sha256 does not match receipt")

    connection = _connect_immutable(snapshot)
    try:
        _quick_check(connection, label="backup snapshot")
        page_count = _page_count(connection)
    finally:
        connection.close()

    if _snapshot_identity(snapshot) != snapshot_identity:
        raise StateBackupError("backup snapshot identity changed during verification")
    _require_snapshot_read_only(snapshot)

    second_size, second_sha256 = _hash_file(
        snapshot,
        expected_identity=snapshot_identity,
    )
    if (second_size, second_sha256) != (first_size, first_sha256):
        raise StateBackupError("backup snapshot bytes changed during verification")
    if page_count != receipt.page_count:
        raise StateBackupError(
            f"backup snapshot page_count does not match receipt: "
            f"{page_count} != {receipt.page_count}"
        )

    return receipt


def _validate_backup_timeout(max_seconds: float) -> float:
    if type(max_seconds) not in (int, float):
        raise StateBackupError(
            "backup max_seconds must be a positive finite native number"
        )
    if max_seconds <= 0:
        raise StateBackupError(
            "backup max_seconds must be a positive finite native number"
        )
    try:
        normalized = float(max_seconds)
    except (OverflowError, ValueError) as exc:
        raise StateBackupError(
            "backup max_seconds must be a positive finite native number"
        ) from exc
    if not math.isfinite(normalized):
        raise StateBackupError(
            "backup max_seconds must be a positive finite native number"
        )
    return normalized


def _monotonic() -> float:
    return time.monotonic()


def _run_online_backup(
    source: sqlite3.Connection,
    destination: sqlite3.Connection,
    *,
    max_seconds: float,
) -> None:
    max_seconds = _validate_backup_timeout(max_seconds)
    deadline = _monotonic() + max_seconds

    def progress(status: int, remaining: int, total: int) -> None:
        del status, remaining, total
        if _monotonic() > deadline:
            raise StateBackupError(
                f"SQLite online backup exceeded {max_seconds:g}s deadline"
            )

    try:
        source.backup(
            destination,
            pages=256,
            progress=progress,
            sleep=0.05,
        )
    except sqlite3.Error as exc:
        raise StateBackupError(f"SQLite online backup failed: {exc}") from exc


def _fsync_file(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StateBackupError(f"unable to open snapshot for fsync: {exc}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise StateBackupError(f"unable to fsync snapshot file: {exc}") from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StateBackupError(
            f"unable to open destination directory for fsync: {exc}"
        ) from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise StateBackupError(
            f"unable to fsync destination directory: {exc}"
        ) from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _seal_snapshot_read_only(
    path: Path,
    expected_identity: tuple[int, int],
) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise StateBackupError(
            f"unable to open published snapshot for sealing: {exc}"
        ) from exc

    try:
        file_stat = os.fstat(fd)
        if (file_stat.st_dev, file_stat.st_ino) != expected_identity:
            raise StateBackupError(
                "published snapshot identity changed before read-only sealing"
            )
        try:
            os.fchmod(fd, 0o400)
            os.fsync(fd)
        except OSError as exc:
            raise StateBackupError(
                f"unable to seal published snapshot read-only: {exc}"
            ) from exc
    finally:
        try:
            os.close(fd)
        except OSError:
            pass

    if _path_identity(path) != expected_identity:
        raise StateBackupError(
            "published snapshot identity changed during read-only sealing"
        )
    _require_snapshot_read_only(path)


def _cleanup_temp(path: Path) -> list[str]:
    failures: list[str] = []
    for candidate in (
        path,
        Path(f"{path}-journal"),
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            failures.append(f"{candidate}: {exc}")
    return failures


def _create_temp_snapshot(parent: Path) -> Path:
    try:
        temp_fd, temp_name = tempfile.mkstemp(
            prefix=".haxlab-state-backup-",
            suffix=".sqlite",
            dir=parent,
        )
    except OSError as exc:
        raise StateBackupError(
            f"unable to create private backup temp file in {parent}: {exc}"
        ) from exc
    try:
        os.close(temp_fd)
    except OSError as exc:
        try:
            Path(temp_name).unlink()
        except OSError:
            pass
        raise StateBackupError(f"unable to close backup temp file: {exc}") from exc

    temp_path = Path(temp_name)
    try:
        os.chmod(temp_path, 0o600)
    except OSError as exc:
        cleanup_failures = _cleanup_temp(temp_path)
        detail = (
            f"; cleanup failures={cleanup_failures}" if cleanup_failures else ""
        )
        raise StateBackupError(
            f"unable to set private backup temp permissions: {exc}{detail}"
        ) from exc
    return temp_path


def _rollback_published(
    destination: Path,
    expected_identity: tuple[int, int] | None,
) -> list[str]:
    failures: list[str] = []
    if expected_identity is None:
        return failures

    try:
        current_identity = _path_identity(destination)
    except StateBackupError as exc:
        failures.append(str(exc))
        return failures

    if current_identity != expected_identity:
        return failures

    try:
        destination.unlink()
    except FileNotFoundError:
        return failures
    except OSError as exc:
        failures.append(f"unable to unlink published snapshot during rollback: {exc}")
        return failures

    try:
        _fsync_directory(destination.parent)
    except StateBackupError as exc:
        failures.append(f"rollback directory durability failed: {exc}")

    return failures


def backup_runtime_state(
    source: Path,
    destination: Path,
    *,
    max_seconds: float = 30.0,
) -> BackupReceipt:
    source = _absolute(source)
    destination = _absolute(destination)
    max_seconds = _validate_backup_timeout(max_seconds)

    source_identity = _source_identity(source)
    _validate_destination(source, destination)

    temp_path = _create_temp_snapshot(destination.parent)
    published_identity: tuple[int, int] | None = None

    try:
        source_connection = _connect_read_only(source)
        try:
            if _source_identity(source) != source_identity:
                raise StateBackupError("source database identity changed before backup")
            _quick_check(source_connection, label="source database")

            try:
                destination_connection = sqlite3.connect(temp_path)
                try:
                    _run_online_backup(
                        source_connection,
                        destination_connection,
                        max_seconds=max_seconds,
                    )
                    destination_connection.commit()
                    _quick_check(destination_connection, label="snapshot")
                    page_count = _page_count(destination_connection)
                finally:
                    destination_connection.close()
            except sqlite3.Error as exc:
                raise StateBackupError(
                    f"unable to create SQLite backup destination: {exc}"
                ) from exc

            if _source_identity(source) != source_identity:
                raise StateBackupError("source database identity changed during backup")
        finally:
            source_connection.close()

        _fsync_file(temp_path)
        temp_identity = _path_identity(temp_path)
        size_bytes, sha256 = _hash_file(
            temp_path,
            expected_identity=temp_identity,
        )

        try:
            os.link(temp_path, destination)
            published_identity = temp_identity
        except FileExistsError as exc:
            raise StateBackupError(
                f"destination appeared during backup and was not overwritten: {destination}"
            ) from exc
        except OSError as exc:
            raise StateBackupError(f"unable to publish snapshot atomically: {exc}") from exc

        if _path_identity(destination) != temp_identity:
            raise StateBackupError("published snapshot identity changed before durability sync")
        _fsync_directory(destination.parent)

        temp_path.unlink()
        _fsync_directory(destination.parent)

        if _path_identity(destination) != temp_identity:
            raise StateBackupError("published snapshot identity changed after durability sync")

        _seal_snapshot_read_only(destination, temp_identity)
        _fsync_directory(destination.parent)

        receipt = BackupReceipt(
            schema=RECEIPT_SCHEMA,
            size_bytes=size_bytes,
            sha256=sha256,
            page_count=page_count,
        )
        verify_runtime_state_backup(destination, receipt)
        return receipt
    except StateBackupError as exc:
        rollback_failures = _rollback_published(destination, published_identity)
        cleanup_failures = _cleanup_temp(temp_path)
        details: list[str] = []
        if rollback_failures:
            details.append(f"rollback failures={rollback_failures}")
        if cleanup_failures:
            details.append(f"temporary cleanup failures={cleanup_failures}")
        if details:
            raise StateBackupError(f"{exc}; {'; '.join(details)}") from exc
        raise
    except OSError as exc:
        rollback_failures = _rollback_published(destination, published_identity)
        cleanup_failures = _cleanup_temp(temp_path)
        details: list[str] = []
        if rollback_failures:
            details.append(f"rollback failures={rollback_failures}")
        if cleanup_failures:
            details.append(f"temporary cleanup failures={cleanup_failures}")
        detail = f"; {'; '.join(details)}" if details else ""
        raise StateBackupError(
            f"backup filesystem operation failed: {exc}{detail}"
        ) from exc


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a crash-consistent HaxLab runtime SQLite backup."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=30.0,
        help="Fail closed if SQLite online backup exceeds this deadline (default: 30).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        receipt = backup_runtime_state(
            args.source,
            args.destination,
            max_seconds=args.max_seconds,
        )
    except StateBackupError as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return 2

    print(
        json.dumps(
            {"ok": True, "receipt": asdict(receipt)},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
