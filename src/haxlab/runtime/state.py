from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT,
    status TEXT NOT NULL,
    error TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_files_sha256
    ON source_files(sha256);

CREATE TABLE IF NOT EXISTS raw_replays (
    sha256 TEXT PRIMARY KEY,
    archive_path TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    first_archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runtime_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject TEXT,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True)
class KnownSource:
    source_path: str
    size_bytes: int
    mtime_ns: int
    sha256: str | None
    status: str


class RuntimeState:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "RuntimeState":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def get_source(self, source_path: str) -> KnownSource | None:
        row = self.connection.execute(
            """
            SELECT source_path, size_bytes, mtime_ns, sha256, status
            FROM source_files
            WHERE source_path = ?
            """,
            (source_path,),
        ).fetchone()
        if row is None:
            return None
        return KnownSource(**dict(row))

    def mark_seen(
        self,
        *,
        source_path: str,
        size_bytes: int,
        mtime_ns: int,
        sha256: str | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO source_files (
                source_path, size_bytes, mtime_ns, sha256, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_path) DO UPDATE SET
                size_bytes = excluded.size_bytes,
                mtime_ns = excluded.mtime_ns,
                sha256 = excluded.sha256,
                status = excluded.status,
                error = excluded.error,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (source_path, size_bytes, mtime_ns, sha256, status, error),
        )
        self.connection.commit()

    def raw_exists(self, sha256: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM raw_replays WHERE sha256 = ?",
            (sha256,),
        ).fetchone()
        return row is not None

    def register_raw(
        self,
        *,
        sha256: str,
        archive_path: str,
        size_bytes: int,
    ) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO raw_replays (sha256, archive_path, size_bytes)
            VALUES (?, ?, ?)
            """,
            (sha256, archive_path, size_bytes),
        )
        self.connection.commit()

    def event(self, event_type: str, subject: str = "", detail: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO runtime_events (event_type, subject, detail)
            VALUES (?, ?, ?)
            """,
            (event_type, subject, detail),
        )
        self.connection.commit()
