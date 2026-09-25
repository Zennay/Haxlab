from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


CURRENT_ANALYZER_VERSION = "state-pass-v3"


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

CREATE TABLE IF NOT EXISTS replay_processing (
    sha256 TEXT PRIMARY KEY REFERENCES raw_replays(sha256) ON DELETE CASCADE,
    status TEXT NOT NULL,
    format_version INTEGER,
    total_frames INTEGER,
    duration_seconds REAL,
    decompressed_bytes INTEGER,
    parser_stage TEXT NOT NULL DEFAULT 'probe',
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_processing_status
    ON replay_processing(status);

CREATE TABLE IF NOT EXISTS replay_analysis (
    sha256 TEXT PRIMARY KEY REFERENCES raw_replays(sha256) ON DELETE CASCADE,
    status TEXT NOT NULL,
    analyzer_version TEXT,
    output_path TEXT,
    sampled_state_count INTEGER,
    player_count INTEGER,
    raw_event_count INTEGER,
    tick_count INTEGER,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_replay_analysis_status
    ON replay_analysis(status);

CREATE TABLE IF NOT EXISTS replay_analysis_versions (
    sha256 TEXT NOT NULL REFERENCES raw_replays(sha256) ON DELETE CASCADE,
    analyzer_version TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT,
    sampled_state_count INTEGER,
    player_count INTEGER,
    raw_event_count INTEGER,
    tick_count INTEGER,
    error TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (sha256, analyzer_version)
);

CREATE INDEX IF NOT EXISTS idx_replay_analysis_versions_status
    ON replay_analysis_versions(analyzer_version, status);

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


@dataclass(frozen=True)
class RawReplayRecord:
    sha256: str
    archive_path: str
    size_bytes: int


class RuntimeState:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)
        self._migrate_analysis_versions()

    def _migrate_analysis_versions(self) -> None:
        """Copy legacy single-version rows into the versioned analysis ledger.

        The old table is intentionally retained for rollback/forensics, but all
        new runtime reads and writes use replay_analysis_versions.
        """
        self.connection.execute(
            """
            INSERT OR IGNORE INTO replay_analysis_versions (
                sha256, analyzer_version, status, output_path,
                sampled_state_count, player_count, raw_event_count,
                tick_count, error, updated_at
            )
            SELECT
                sha256,
                COALESCE(analyzer_version, 'legacy'),
                status,
                output_path,
                sampled_state_count,
                player_count,
                raw_event_count,
                tick_count,
                error,
                updated_at
            FROM replay_analysis
            """
        )
        self.connection.commit()

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

    def list_unprocessed_replays(self, limit: int = 100) -> list[RawReplayRecord]:
        rows = self.connection.execute(
            """
            SELECT r.sha256, r.archive_path, r.size_bytes
            FROM raw_replays AS r
            LEFT JOIN replay_processing AS p ON p.sha256 = r.sha256
            WHERE p.sha256 IS NULL
            ORDER BY r.first_archived_at, r.sha256
            LIMIT ?
            """,
            (max(1, limit),),
        ).fetchall()
        return [RawReplayRecord(**dict(row)) for row in rows]

    def list_unanalyzed_replays(
        self,
        limit: int = 100,
        analyzer_version: str = CURRENT_ANALYZER_VERSION,
    ) -> list[RawReplayRecord]:
        rows = self.connection.execute(
            """
            SELECT r.sha256, r.archive_path, r.size_bytes
            FROM raw_replays AS r
            JOIN replay_processing AS p
              ON p.sha256 = r.sha256 AND p.status = 'ok'
            LEFT JOIN replay_analysis_versions AS a
              ON a.sha256 = r.sha256 AND a.analyzer_version = ?
            WHERE a.sha256 IS NULL OR a.status = 'retry'
            ORDER BY r.first_archived_at, r.sha256
            LIMIT ?
            """,
            (analyzer_version, max(1, limit)),
        ).fetchall()
        return [RawReplayRecord(**dict(row)) for row in rows]

    def mark_replay_processing(
        self,
        *,
        sha256: str,
        status: str,
        format_version: int | None = None,
        total_frames: int | None = None,
        duration_seconds: float | None = None,
        decompressed_bytes: int | None = None,
        parser_stage: str = "probe",
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO replay_processing (
                sha256, status, format_version, total_frames, duration_seconds,
                decompressed_bytes, parser_stage, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256) DO UPDATE SET
                status = excluded.status,
                format_version = excluded.format_version,
                total_frames = excluded.total_frames,
                duration_seconds = excluded.duration_seconds,
                decompressed_bytes = excluded.decompressed_bytes,
                parser_stage = excluded.parser_stage,
                error = excluded.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                sha256,
                status,
                format_version,
                total_frames,
                duration_seconds,
                decompressed_bytes,
                parser_stage,
                error,
            ),
        )
        self.connection.commit()

    def mark_replay_analysis(
        self,
        *,
        sha256: str,
        status: str,
        analyzer_version: str = CURRENT_ANALYZER_VERSION,
        output_path: str | None = None,
        sampled_state_count: int | None = None,
        player_count: int | None = None,
        raw_event_count: int | None = None,
        tick_count: int | None = None,
        error: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO replay_analysis_versions (
                sha256, analyzer_version, status, output_path,
                sampled_state_count, player_count, raw_event_count,
                tick_count, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sha256, analyzer_version) DO UPDATE SET
                status = excluded.status,
                output_path = excluded.output_path,
                sampled_state_count = excluded.sampled_state_count,
                player_count = excluded.player_count,
                raw_event_count = excluded.raw_event_count,
                tick_count = excluded.tick_count,
                error = excluded.error,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                sha256,
                analyzer_version,
                status,
                output_path,
                sampled_state_count,
                player_count,
                raw_event_count,
                tick_count,
                error,
            ),
        )
        self.connection.commit()

    def status_snapshot(self) -> dict[str, object]:
        source = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM source_files GROUP BY status"
            )
        }
        raw_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM raw_replays"
        ).fetchone()["count"]
        processed = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                "SELECT status, COUNT(*) AS count FROM replay_processing GROUP BY status"
            )
        }
        analyzed = {
            row["status"]: row["count"]
            for row in self.connection.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM replay_analysis_versions
                WHERE analyzer_version = ?
                GROUP BY status
                """,
                (CURRENT_ANALYZER_VERSION,),
            )
        }
        analysis_versions: dict[str, dict[str, int]] = {}
        for row in self.connection.execute(
            """
            SELECT analyzer_version, status, COUNT(*) AS count
            FROM replay_analysis_versions
            GROUP BY analyzer_version, status
            ORDER BY analyzer_version, status
            """
        ):
            version = str(row["analyzer_version"])
            analysis_versions.setdefault(version, {})[str(row["status"])] = int(
                row["count"]
            )

        totals = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(total_frames), 0) AS total_frames,
                COALESCE(SUM(duration_seconds), 0.0) AS duration_seconds
            FROM replay_processing
            WHERE status = 'ok'
            """
        ).fetchone()
        analysis_totals = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(sampled_state_count), 0) AS samples,
                COALESCE(SUM(raw_event_count), 0) AS events,
                COALESCE(SUM(tick_count), 0) AS ticks
            FROM replay_analysis_versions
            WHERE status = 'ok' AND analyzer_version = ?
            """,
            (CURRENT_ANALYZER_VERSION,),
        ).fetchone()
        processed_total = sum(processed.values())
        analyzed_total = int(analyzed.get("ok", 0)) + int(analyzed.get("failed", 0))

        recent_processed = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM replay_processing
            WHERE updated_at >= datetime('now', '-5 minutes')
            """
        ).fetchone()["count"]
        recent_analyzed = self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM replay_analysis_versions
            WHERE updated_at >= datetime('now', '-5 minutes')
              AND analyzer_version = ?
            """,
            (CURRENT_ANALYZER_VERSION,),
        ).fetchone()["count"]

        probe_rate_per_minute = float(recent_processed) / 5.0
        analysis_rate_per_minute = float(recent_analyzed) / 5.0

        processing_ok = int(processed.get("ok", 0))

        return {
            "analysis_version": CURRENT_ANALYZER_VERSION,
            "analysis_versions": analysis_versions,
            "source_archived": int(source.get("archived", 0)),
            "source_duplicates": int(source.get("duplicate", 0)),
            "source_failed": int(source.get("failed", 0)),
            "raw_unique_replays": int(raw_count),
            "processing_ok": processing_ok,
            "processing_failed": int(processed.get("failed", 0)),
            "processing_pending": max(0, int(raw_count) - int(processed_total)),
            "total_frames_probed": int(totals["total_frames"]),
            "duration_seconds_probed": float(totals["duration_seconds"]),
            "probe_rate_per_minute_5m": round(probe_rate_per_minute, 3),
            "analysis_ok": int(analyzed.get("ok", 0)),
            "analysis_failed": int(analyzed.get("failed", 0)),
            "analysis_pending": max(0, processing_ok - int(analyzed_total)),
            "analysis_sampled_states": int(analysis_totals["samples"]),
            "analysis_raw_events": int(analysis_totals["events"]),
            "analysis_ticks_reconstructed": int(analysis_totals["ticks"]),
            "analysis_rate_per_minute_5m": round(analysis_rate_per_minute, 3),
        }

    def event(self, event_type: str, subject: str = "", detail: str = "") -> None:
        self.connection.execute(
            """
            INSERT INTO runtime_events (event_type, subject, detail)
            VALUES (?, ?, ?)
            """,
            (event_type, subject, detail),
        )
        self.connection.commit()
