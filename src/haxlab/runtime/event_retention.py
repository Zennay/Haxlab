from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence


SCHEMA_VERSION = "haxlab-runtime-event-retention-v1"
EXPECTED_COLUMNS = ("id", "event_type", "subject", "detail", "created_at")


class EventRetentionError(RuntimeError):
    """Raised when runtime-event maintenance cannot be performed safely."""


@dataclass(frozen=True)
class RetentionReceipt:
    schema_version: str
    dry_run: bool
    keep_hours: int
    keep_latest: int
    evaluated_at_utc: str
    cutoff_utc: str
    rows_before: int
    rows_eligible: int
    rows_deleted: int
    rows_after: int
    first_eligible_id: int | None
    last_eligible_id: int | None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _canonical_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise EventRetentionError("evaluated_at must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _format_sqlite_timestamp(value: datetime) -> str:
    return _canonical_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _format_receipt_timestamp(value: datetime) -> str:
    return _canonical_utc(value).isoformat().replace("+00:00", "Z")


def _validate_db_path(path: Path) -> Path:
    candidate = Path(path)
    try:
        info = candidate.lstat()
    except FileNotFoundError as exc:
        raise EventRetentionError(f"state database does not exist: {candidate}") from exc

    if stat.S_ISLNK(info.st_mode):
        raise EventRetentionError("state database path must not be a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise EventRetentionError("state database path must be a regular file")

    resolved = candidate.resolve(strict=True)
    if resolved != candidate.absolute():
        raise EventRetentionError("state database path must not traverse symlinks")
    return resolved


def _validate_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT type FROM sqlite_master WHERE name = 'runtime_events'"
    ).fetchone()
    if row is None or row[0] != "table":
        raise EventRetentionError("runtime_events table is missing")

    columns = tuple(
        str(item[1])
        for item in connection.execute("PRAGMA table_info(runtime_events)").fetchall()
    )
    if columns != EXPECTED_COLUMNS:
        raise EventRetentionError(
            "runtime_events schema mismatch: "
            f"expected={EXPECTED_COLUMNS!r} actual={columns!r}"
        )


def apply_event_retention(
    state_db: Path,
    *,
    keep_hours: int = 168,
    keep_latest: int = 10_000,
    dry_run: bool = False,
    evaluated_at: datetime | None = None,
) -> RetentionReceipt:
    """Delete only old runtime events outside the newest-row safety floor."""

    if isinstance(keep_hours, bool) or keep_hours < 1:
        raise EventRetentionError("keep_hours must be an integer >= 1")
    if isinstance(keep_latest, bool) or keep_latest < 0:
        raise EventRetentionError("keep_latest must be an integer >= 0")

    database = _validate_db_path(Path(state_db))
    now = _canonical_utc(evaluated_at or datetime.now(timezone.utc))
    cutoff = now - timedelta(hours=keep_hours)

    connection = sqlite3.connect(database, timeout=30.0)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        _validate_schema(connection)

        rows_before = int(
            connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0]
        )

        eligible = connection.execute(
            """
            WITH newest AS (
                SELECT id
                FROM runtime_events
                ORDER BY created_at DESC, id DESC
                LIMIT ?
            )
            SELECT COUNT(*), MIN(id), MAX(id)
            FROM runtime_events
            WHERE created_at < ?
              AND id NOT IN (SELECT id FROM newest)
            """,
            (keep_latest, _format_sqlite_timestamp(cutoff)),
        ).fetchone()
        rows_eligible = int(eligible[0])
        first_eligible_id = int(eligible[1]) if eligible[1] is not None else None
        last_eligible_id = int(eligible[2]) if eligible[2] is not None else None

        if not dry_run and rows_eligible:
            connection.execute(
                """
                WITH newest AS (
                    SELECT id
                    FROM runtime_events
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                )
                DELETE FROM runtime_events
                WHERE created_at < ?
                  AND id NOT IN (SELECT id FROM newest)
                """,
                (keep_latest, _format_sqlite_timestamp(cutoff)),
            )

        rows_after = rows_before if dry_run else rows_before - rows_eligible
        if dry_run:
            connection.rollback()
        else:
            connection.commit()

        return RetentionReceipt(
            schema_version=SCHEMA_VERSION,
            dry_run=dry_run,
            keep_hours=keep_hours,
            keep_latest=keep_latest,
            evaluated_at_utc=_format_receipt_timestamp(now),
            cutoff_utc=_format_receipt_timestamp(cutoff),
            rows_before=rows_before,
            rows_eligible=rows_eligible,
            rows_deleted=0 if dry_run else rows_eligible,
            rows_after=rows_after,
            first_eligible_id=first_eligible_id,
            last_eligible_id=last_eligible_id,
        )
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bound HaxLab runtime_events growth with fail-closed retention."
    )
    parser.add_argument("state_db", type=Path)
    parser.add_argument("--keep-hours", type=int, default=168)
    parser.add_argument("--keep-latest", type=int, default=10_000)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        receipt = apply_event_retention(
            args.state_db,
            keep_hours=args.keep_hours,
            keep_latest=args.keep_latest,
            dry_run=args.dry_run,
        )
    except (EventRetentionError, sqlite3.Error, OSError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "schema_version": SCHEMA_VERSION,
                    "error": str(exc),
                },
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            {"ok": True, **receipt.as_dict()},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
