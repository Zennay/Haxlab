from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


AUDIT_SCHEMA = "haxlab-runtime-state-audit-v1"
_REQUIRED_TABLES = (
    "source_files",
    "raw_replays",
    "replay_processing",
    "replay_analysis",
    "replay_analysis_versions",
    "runtime_events",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_STATUSES = {"archived", "duplicate", "failed"}
_PROCESSING_STATUSES = {"ok", "failed"}
_ANALYSIS_STATUSES = {"ok", "failed", "retry"}


@dataclass(frozen=True)
class AuditIssue:
    code: str
    subject: str
    detail: str


@dataclass(frozen=True)
class RuntimeStateAudit:
    schema: str
    ok: bool
    database_path: str
    tables: tuple[str, ...]
    issues: tuple[AuditIssue, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "database_path": self.database_path,
            "tables": list(self.tables),
            "issue_count": len(self.issues),
            "issues": [asdict(issue) for issue in self.issues],
        }


def _valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _valid_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _valid_nonnegative_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) >= 0.0
    )


def _nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _append_issue(
    issues: list[AuditIssue],
    code: str,
    subject: object,
    detail: str,
) -> None:
    issues.append(AuditIssue(code=code, subject=str(subject), detail=detail))


def _table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        ORDER BY name
        """
    ).fetchall()
    return tuple(str(row["name"]) for row in rows)


def _audit_source_files(
    connection: sqlite3.Connection,
    issues: list[AuditIssue],
) -> None:
    for row in connection.execute(
        """
        SELECT source_path, size_bytes, mtime_ns, sha256, status, error
        FROM source_files
        ORDER BY source_path
        """
    ):
        subject = row["source_path"]
        status = row["status"]
        if not _nonempty_text(subject):
            _append_issue(issues, "source_path_invalid", subject, "source_path must be non-empty")
        if not _valid_nonnegative_int(row["size_bytes"]):
            _append_issue(issues, "source_size_invalid", subject, "size_bytes must be a non-negative integer")
        if not _valid_nonnegative_int(row["mtime_ns"]):
            _append_issue(issues, "source_mtime_invalid", subject, "mtime_ns must be a non-negative integer")
        if status not in _SOURCE_STATUSES:
            _append_issue(
                issues,
                "source_status_invalid",
                subject,
                f"unsupported source status: {status!r}",
            )
            continue
        if status in {"archived", "duplicate"}:
            if not _valid_sha256(row["sha256"]):
                _append_issue(
                    issues,
                    "source_sha256_invalid",
                    subject,
                    "archived/duplicate source requires canonical lowercase sha256",
                )
            if row["error"] is not None:
                _append_issue(
                    issues,
                    "source_success_has_error",
                    subject,
                    "archived/duplicate source must not retain error text",
                )
        elif status == "failed":
            if not _nonempty_text(row["error"]):
                _append_issue(
                    issues,
                    "source_failure_missing_error",
                    subject,
                    "failed source requires non-empty error evidence",
                )

    for row in connection.execute(
        """
        SELECT s.source_path, s.sha256
        FROM source_files AS s
        LEFT JOIN raw_replays AS r ON r.sha256 = s.sha256
        WHERE s.status IN ('archived', 'duplicate')
          AND s.sha256 IS NOT NULL
          AND r.sha256 IS NULL
        ORDER BY s.source_path
        """
    ):
        _append_issue(
            issues,
            "source_without_raw_replay",
            row["source_path"],
            f"source references unknown raw replay sha256 {row['sha256']!r}",
        )

    for row in connection.execute(
        """
        SELECT
            s.source_path,
            s.size_bytes AS source_size_bytes,
            r.size_bytes AS raw_size_bytes
        FROM source_files AS s
        JOIN raw_replays AS r ON r.sha256 = s.sha256
        WHERE s.status IN ('archived', 'duplicate')
          AND s.size_bytes != r.size_bytes
        ORDER BY s.source_path
        """
    ):
        _append_issue(
            issues,
            "source_raw_size_mismatch",
            row["source_path"],
            (
                f"source size {row['source_size_bytes']!r} does not match "
                f"raw replay size {row['raw_size_bytes']!r}"
            ),
        )


def _audit_raw_replays(
    connection: sqlite3.Connection,
    issues: list[AuditIssue],
) -> None:
    for row in connection.execute(
        """
        SELECT sha256, archive_path, size_bytes
        FROM raw_replays
        ORDER BY sha256
        """
    ):
        subject = row["sha256"]
        if not _valid_sha256(subject):
            _append_issue(
                issues,
                "raw_sha256_invalid",
                subject,
                "raw replay key must be canonical lowercase sha256",
            )
        if not _nonempty_text(row["archive_path"]):
            _append_issue(
                issues,
                "raw_archive_path_invalid",
                subject,
                "archive_path must be non-empty",
            )
        if not _valid_nonnegative_int(row["size_bytes"]):
            _append_issue(
                issues,
                "raw_size_invalid",
                subject,
                "size_bytes must be a non-negative integer",
            )


def _audit_processing(
    connection: sqlite3.Connection,
    issues: list[AuditIssue],
) -> None:
    for row in connection.execute(
        """
        SELECT
            sha256, status, format_version, total_frames, duration_seconds,
            decompressed_bytes, parser_stage, error
        FROM replay_processing
        ORDER BY sha256
        """
    ):
        subject = row["sha256"]
        status = row["status"]
        if not _valid_sha256(subject):
            _append_issue(
                issues,
                "processing_sha256_invalid",
                subject,
                "processing row key must be canonical lowercase sha256",
            )
        if status not in _PROCESSING_STATUSES:
            _append_issue(
                issues,
                "processing_status_invalid",
                subject,
                f"unsupported processing status: {status!r}",
            )
            continue
        if not _nonempty_text(row["parser_stage"]):
            _append_issue(
                issues,
                "processing_stage_invalid",
                subject,
                "parser_stage must be non-empty",
            )
        if status == "ok":
            if not isinstance(row["format_version"], int) or isinstance(
                row["format_version"], bool
            ) or row["format_version"] <= 0:
                _append_issue(
                    issues,
                    "processing_format_version_invalid",
                    subject,
                    "successful processing requires a positive integer format_version",
                )
            for column in ("total_frames", "decompressed_bytes"):
                if not _valid_nonnegative_int(row[column]):
                    _append_issue(
                        issues,
                        f"processing_{column}_invalid",
                        subject,
                        f"successful processing requires non-negative integer {column}",
                    )
            if not _valid_nonnegative_number(row["duration_seconds"]):
                _append_issue(
                    issues,
                    "processing_duration_invalid",
                    subject,
                    "successful processing requires finite non-negative duration_seconds",
                )
            if row["error"] is not None:
                _append_issue(
                    issues,
                    "processing_success_has_error",
                    subject,
                    "successful processing must not retain error text",
                )
        elif not _nonempty_text(row["error"]):
            _append_issue(
                issues,
                "processing_failure_missing_error",
                subject,
                "failed processing requires non-empty error evidence",
            )


def _audit_analysis(
    connection: sqlite3.Connection,
    issues: list[AuditIssue],
) -> None:
    for row in connection.execute(
        """
        SELECT
            sha256, analyzer_version, status, output_path,
            sampled_state_count, player_count, raw_event_count, tick_count, error
        FROM replay_analysis_versions
        ORDER BY analyzer_version, sha256
        """
    ):
        subject = f"{row['analyzer_version']}:{row['sha256']}"
        status = row["status"]
        if not _valid_sha256(row["sha256"]):
            _append_issue(
                issues,
                "analysis_sha256_invalid",
                subject,
                "analysis row key must be canonical lowercase sha256",
            )
        if not _nonempty_text(row["analyzer_version"]):
            _append_issue(
                issues,
                "analysis_version_invalid",
                subject,
                "analyzer_version must be non-empty",
            )
        if status not in _ANALYSIS_STATUSES:
            _append_issue(
                issues,
                "analysis_status_invalid",
                subject,
                f"unsupported analysis status: {status!r}",
            )
            continue
        if status == "ok":
            if not _nonempty_text(row["output_path"]):
                _append_issue(
                    issues,
                    "analysis_output_path_invalid",
                    subject,
                    "successful analysis requires non-empty output_path",
                )
            for column in (
                "sampled_state_count",
                "player_count",
                "raw_event_count",
                "tick_count",
            ):
                if not _valid_nonnegative_int(row[column]):
                    _append_issue(
                        issues,
                        f"analysis_{column}_invalid",
                        subject,
                        f"successful analysis requires non-negative integer {column}",
                    )
            if row["error"] is not None:
                _append_issue(
                    issues,
                    "analysis_success_has_error",
                    subject,
                    "successful analysis must not retain error text",
                )
        elif status == "failed" and not _nonempty_text(row["error"]):
            _append_issue(
                issues,
                "analysis_failure_missing_error",
                subject,
                "failed analysis requires non-empty error evidence",
            )

    for row in connection.execute(
        """
        SELECT a.sha256, a.analyzer_version, a.status, p.status AS processing_status
        FROM replay_analysis_versions AS a
        LEFT JOIN replay_processing AS p ON p.sha256 = a.sha256
        WHERE p.sha256 IS NULL OR p.status != 'ok'
        ORDER BY a.analyzer_version, a.sha256
        """
    ):
        subject = f"{row['analyzer_version']}:{row['sha256']}"
        _append_issue(
            issues,
            "analysis_without_successful_processing",
            subject,
            f"analysis status {row['status']!r} is bound to processing status {row['processing_status']!r}",
        )

    for row in connection.execute(
        """
        SELECT output_path, COUNT(*) AS row_count
        FROM replay_analysis_versions
        WHERE status = 'ok'
          AND output_path IS NOT NULL
          AND TRIM(output_path) != ''
        GROUP BY output_path
        HAVING COUNT(*) > 1
        ORDER BY output_path
        """
    ):
        _append_issue(
            issues,
            "analysis_output_path_reused",
            row["output_path"],
            f"successful analysis output is claimed by {int(row['row_count'])} ledger rows",
        )


def audit_runtime_state(path: Path) -> RuntimeStateAudit:
    database_path = str(path)
    issues: list[AuditIssue] = []

    if not path.exists():
        _append_issue(
            issues,
            "database_missing",
            database_path,
            "runtime state database does not exist",
        )
        return RuntimeStateAudit(
            schema=AUDIT_SCHEMA,
            ok=False,
            database_path=database_path,
            tables=(),
            issues=tuple(issues),
        )
    if path.is_symlink() or not path.is_file():
        _append_issue(
            issues,
            "database_path_unsafe",
            database_path,
            "runtime state database must be a regular non-symlink file",
        )
        return RuntimeStateAudit(
            schema=AUDIT_SCHEMA,
            ok=False,
            database_path=database_path,
            tables=(),
            issues=tuple(issues),
        )

    tables: tuple[str, ...] = ()
    try:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            tables = _table_names(connection)
            missing = [name for name in _REQUIRED_TABLES if name not in tables]
            for name in missing:
                _append_issue(
                    issues,
                    "required_table_missing",
                    name,
                    "required runtime-state table is absent",
                )

            quick_check_rows = connection.execute("PRAGMA quick_check").fetchall()
            for row in quick_check_rows:
                result = str(row[0])
                if result != "ok":
                    _append_issue(
                        issues,
                        "sqlite_quick_check_failed",
                        database_path,
                        result,
                    )

            for row in connection.execute("PRAGMA foreign_key_check"):
                _append_issue(
                    issues,
                    "foreign_key_violation",
                    f"{row[0]}:{row[1]}",
                    f"parent={row[2]!r};fk_index={row[3]!r}",
                )

            if "source_files" in tables:
                _audit_source_files(connection, issues)
            if "raw_replays" in tables:
                _audit_raw_replays(connection, issues)
            if "replay_processing" in tables:
                _audit_processing(connection, issues)
            if "replay_analysis_versions" in tables:
                _audit_analysis(connection, issues)
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError) as exc:
        _append_issue(
            issues,
            "database_unreadable",
            database_path,
            f"{type(exc).__name__}:{exc}",
        )

    ordered = tuple(sorted(issues, key=lambda item: (item.code, item.subject, item.detail)))
    return RuntimeStateAudit(
        schema=AUDIT_SCHEMA,
        ok=not ordered,
        database_path=database_path,
        tables=tables,
        issues=ordered,
    )


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed read-only audit of the HaxLab runtime SQLite ledger."
    )
    parser.add_argument("state_db", type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = audit_runtime_state(args.state_db)
    print(json.dumps(result.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
