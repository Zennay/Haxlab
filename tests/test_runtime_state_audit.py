from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState
from haxlab.runtime import state_audit
from haxlab.runtime.state_audit import AUDIT_SCHEMA, audit_runtime_state, main


def _healthy_state(db: Path, tmp_path: Path, *, sha: str = "a" * 64) -> None:
    replay = tmp_path / f"{sha[:8]}.hbr2"
    replay.write_bytes(b"x")
    output = tmp_path / "derived" / CURRENT_ANALYZER_VERSION / f"{sha}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("{}", encoding="utf-8")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=1024,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(output),
            sampled_state_count=100,
            player_count=8,
            raw_event_count=40,
            tick_count=600,
        )


def test_runtime_state_audit_accepts_healthy_ledger(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path)

    result = audit_runtime_state(db)

    assert result.schema == AUDIT_SCHEMA
    assert result.ok is True
    assert result.issues == ()
    assert "raw_replays" in result.tables
    assert "replay_analysis_versions" in result.tables


def test_runtime_state_audit_rejects_analysis_without_successful_processing(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    replay = tmp_path / "failed.hbr2"
    replay.write_bytes(b"x")
    sha = "b" * 64

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="failed",
            error="bad replay payload",
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(tmp_path / "impossible.json"),
            sampled_state_count=1,
            player_count=1,
            raw_event_count=1,
            tick_count=1,
        )

    result = audit_runtime_state(db)
    codes = {issue.code for issue in result.issues}

    assert result.ok is False
    assert "analysis_without_successful_processing" in codes


def test_runtime_state_audit_rejects_malformed_semantic_rows(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path, sha="c" * 64)

    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO source_files (
                source_path, size_bytes, mtime_ns, sha256, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("bad-source.hbr2", -1, -1, "NOT-A-SHA", "archived", "stale error"),
        )
        connection.execute(
            """
            UPDATE replay_analysis_versions
            SET sampled_state_count = -1,
                output_path = '',
                error = 'stale success error'
            WHERE sha256 = ?
            """,
            ("c" * 64,),
        )
        connection.commit()

    result = audit_runtime_state(db)
    codes = {issue.code for issue in result.issues}

    assert result.ok is False
    assert {
        "source_size_invalid",
        "source_mtime_invalid",
        "source_sha256_invalid",
        "source_success_has_error",
        "analysis_sampled_state_count_invalid",
        "analysis_output_path_invalid",
        "analysis_success_has_error",
    } <= codes


def test_runtime_state_audit_preserves_failure_provenance(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path, sha="2" * 64)

    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO source_files (
                source_path, size_bytes, mtime_ns, sha256, status, error
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(tmp_path / "failed-source.hbr2"),
                1,
                1,
                "3" * 64,
                "failed",
                "hashing failed",
            ),
        )
        connection.execute(
            """
            UPDATE replay_analysis_versions
            SET status = 'retry',
                error = NULL
            WHERE sha256 = ?
              AND analyzer_version = ?
            """,
            ("2" * 64, CURRENT_ANALYZER_VERSION),
        )
        connection.commit()

    result = audit_runtime_state(db)
    codes = {issue.code for issue in result.issues}

    assert result.ok is False
    assert "source_failure_has_sha256" in codes
    assert "analysis_retry_missing_error" in codes


def test_runtime_state_audit_reports_missing_and_unsafe_database_paths(
    tmp_path: Path,
) -> None:
    missing = audit_runtime_state(tmp_path / "missing.sqlite3")
    assert missing.ok is False
    assert [issue.code for issue in missing.issues] == ["database_missing"]

    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path)
    link = tmp_path / "state-link.sqlite3"
    link.symlink_to(db)

    unsafe = audit_runtime_state(link)
    assert unsafe.ok is False
    assert [issue.code for issue in unsafe.issues] == ["database_path_unsafe"]


def test_runtime_state_audit_reports_missing_schema_without_crashing(
    tmp_path: Path,
) -> None:
    db = tmp_path / "empty.sqlite3"
    with sqlite3.connect(db):
        pass

    result = audit_runtime_state(db)
    codes = [issue.code for issue in result.issues]

    assert result.ok is False
    assert codes.count("required_table_missing") == 6


def test_runtime_state_audit_requires_successful_source_to_resolve_raw_replay(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    with RuntimeState(db) as state:
        state.mark_seen(
            source_path=str(tmp_path / "orphan-source.hbr2"),
            size_bytes=123,
            mtime_ns=456,
            sha256="d" * 64,
            status="archived",
        )

    result = audit_runtime_state(db)
    codes = {issue.code for issue in result.issues}

    assert result.ok is False
    assert "source_without_raw_replay" in codes


def test_runtime_state_audit_rejects_source_raw_size_mismatch(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    replay = tmp_path / "size-mismatch.hbr2"
    replay.write_bytes(b"x")
    sha = "1" * 64

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=sha,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_seen(
            source_path=str(replay),
            size_bytes=2,
            mtime_ns=123,
            sha256=sha,
            status="duplicate",
        )

    result = audit_runtime_state(db)
    codes = {issue.code for issue in result.issues}

    assert result.ok is False
    assert "source_raw_size_mismatch" in codes


def test_runtime_state_audit_rejects_reused_success_output_path(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    shared_output = tmp_path / "derived" / "shared.json"
    shared_output.parent.mkdir(parents=True)
    shared_output.write_text("{}", encoding="utf-8")

    with RuntimeState(db) as state:
        for sha in ("e" * 64, "f" * 64):
            replay = tmp_path / f"{sha[:8]}.hbr2"
            replay.write_bytes(b"x")
            state.register_raw(
                sha256=sha,
                archive_path=str(replay),
                size_bytes=1,
            )
            state.mark_replay_processing(
                sha256=sha,
                status="ok",
                format_version=3,
                total_frames=600,
                duration_seconds=10.0,
                decompressed_bytes=1024,
            )
            state.mark_replay_analysis(
                sha256=sha,
                analyzer_version=CURRENT_ANALYZER_VERSION,
                status="ok",
                output_path=str(shared_output),
                sampled_state_count=100,
                player_count=8,
                raw_event_count=40,
                tick_count=600,
            )

    result = audit_runtime_state(db)
    reused = [
        issue for issue in result.issues
        if issue.code == "analysis_output_path_reused"
    ]

    assert result.ok is False
    assert len(reused) == 1
    assert reused[0].subject == str(shared_output)


def test_runtime_state_audit_uses_one_consistent_live_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path)

    original = state_audit._audit_source_files
    injected = False

    def inject_concurrent_write(connection, issues) -> None:
        nonlocal injected
        original(connection, issues)
        with sqlite3.connect(db) as writer:
            writer.execute(
                """
                INSERT INTO raw_replays (sha256, archive_path, size_bytes)
                VALUES (?, ?, ?)
                """,
                ("NOT-A-SHA", str(tmp_path / "late.hbr2"), -1),
            )
            writer.commit()
        injected = True

    monkeypatch.setattr(
        state_audit,
        "_audit_source_files",
        inject_concurrent_write,
    )

    first = audit_runtime_state(db)

    assert injected is True
    assert first.ok is True

    monkeypatch.setattr(
        state_audit,
        "_audit_source_files",
        original,
    )
    second = audit_runtime_state(db)
    second_codes = {issue.code for issue in second.issues}

    assert second.ok is False
    assert "raw_sha256_invalid" in second_codes
    assert "raw_size_invalid" in second_codes


def test_runtime_state_audit_cli_is_machine_readable(
    tmp_path: Path,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    _healthy_state(db, tmp_path)

    exit_code = main([str(db)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema"] == AUDIT_SCHEMA
    assert payload["ok"] is True
    assert payload["issue_count"] == 0
