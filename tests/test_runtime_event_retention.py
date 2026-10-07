from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from haxlab.runtime.event_retention import (
    EventRetentionError,
    apply_event_retention,
    main,
)


SCHEMA = """
CREATE TABLE runtime_events (
    id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject TEXT,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def _db(tmp_path: Path) -> Path:
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
    return path


def _insert(path: Path, created_at: str, count: int) -> None:
    with sqlite3.connect(path) as connection:
        connection.executemany(
            """
            INSERT INTO runtime_events (event_type, subject, detail, created_at)
            VALUES ('replay_archived', '', '', ?)
            """,
            [(created_at,)] * count,
        )


def _ids(path: Path) -> list[int]:
    with sqlite3.connect(path) as connection:
        return [
            int(row[0])
            for row in connection.execute(
                "SELECT id FROM runtime_events ORDER BY id"
            ).fetchall()
        ]


def test_retention_keeps_recent_rows_and_latest_floor(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2026-09-01 00:00:00", 5)
    _insert(path, "2026-10-07 03:30:00", 2)

    receipt = apply_event_retention(
        path,
        keep_hours=24,
        keep_latest=3,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )

    assert receipt.rows_before == 7
    assert receipt.rows_eligible == 4
    assert receipt.rows_selected == 4
    assert receipt.rows_deleted == 4
    assert receipt.rows_remaining_eligible == 0
    assert receipt.rows_after == 3
    assert receipt.first_selected_id == 1
    assert receipt.last_selected_id == 4
    assert _ids(path) == [5, 6, 7]


def test_delete_batch_is_bounded_and_oldest_first(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2026-08-01 00:00:00", 3)
    _insert(path, "2026-09-01 00:00:00", 3)

    first = apply_event_retention(
        path,
        keep_hours=24,
        keep_latest=0,
        max_delete=2,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )

    assert first.rows_eligible == 6
    assert first.rows_selected == 2
    assert first.rows_deleted == 2
    assert first.rows_remaining_eligible == 4
    assert first.first_selected_id == 1
    assert first.last_selected_id == 2
    assert _ids(path) == [3, 4, 5, 6]

    second = apply_event_retention(
        path,
        keep_hours=24,
        keep_latest=0,
        max_delete=2,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )
    assert second.first_selected_id == 3
    assert second.last_selected_id == 4
    assert second.rows_remaining_eligible == 2
    assert _ids(path) == [5, 6]


def test_large_batch_does_not_depend_on_sqlite_bind_variable_limit(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2000-01-01 00:00:00", 1_205)

    receipt = apply_event_retention(
        path,
        keep_hours=1,
        keep_latest=0,
        max_delete=1_200,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )

    assert receipt.rows_eligible == 1_205
    assert receipt.rows_selected == 1_200
    assert receipt.rows_deleted == 1_200
    assert receipt.rows_remaining_eligible == 5
    assert _ids(path) == [1201, 1202, 1203, 1204, 1205]


def test_equal_timestamps_use_id_as_deterministic_tiebreaker(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2000-01-01 00:00:00", 5)

    receipt = apply_event_retention(
        path,
        keep_hours=1,
        keep_latest=0,
        max_delete=3,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )

    assert receipt.first_selected_id == 1
    assert receipt.last_selected_id == 3
    assert _ids(path) == [4, 5]


def test_dry_run_reports_same_bounded_deletion_set_without_mutation(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2026-09-01 00:00:00", 4)
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)

    dry = apply_event_retention(
        path,
        keep_hours=1,
        keep_latest=0,
        max_delete=2,
        dry_run=True,
        evaluated_at=now,
    )
    assert dry.rows_eligible == 4
    assert dry.rows_selected == 2
    assert dry.rows_deleted == 0
    assert dry.rows_remaining_eligible == 2
    assert dry.rows_after == 4
    assert dry.first_selected_id == 1
    assert dry.last_selected_id == 2
    assert _ids(path) == [1, 2, 3, 4]

    live = apply_event_retention(
        path,
        keep_hours=1,
        keep_latest=0,
        max_delete=2,
        evaluated_at=now,
    )
    assert live.rows_selected == dry.rows_selected
    assert live.first_selected_id == dry.first_selected_id
    assert live.last_selected_id == dry.last_selected_id
    assert _ids(path) == [3, 4]


def test_boundary_row_at_cutoff_is_retained(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2026-10-06 04:00:00", 1)
    _insert(path, "2026-10-06 03:59:59", 1)

    receipt = apply_event_retention(
        path,
        keep_hours=24,
        keep_latest=0,
        evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
    )

    assert receipt.rows_deleted == 1
    assert _ids(path) == [1]


def test_second_run_is_idempotent_after_all_eligible_rows_removed(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "2026-09-01 00:00:00", 3)
    now = datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc)

    first = apply_event_retention(
        path, keep_hours=24, keep_latest=1, max_delete=10, evaluated_at=now
    )
    second = apply_event_retention(
        path, keep_hours=24, keep_latest=1, max_delete=10, evaluated_at=now
    )

    assert first.rows_deleted == 2
    assert second.rows_deleted == 0
    assert second.rows_selected == 0
    assert second.rows_before == 1
    assert second.rows_after == 1


def test_symlink_database_is_rejected(tmp_path: Path) -> None:
    path = _db(tmp_path)
    link = tmp_path / "state-link.sqlite3"
    link.symlink_to(path)

    with pytest.raises(EventRetentionError, match="symlink"):
        apply_event_retention(link)


def test_schema_drift_is_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "bad.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE runtime_events (id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO runtime_events (event_type) VALUES ('replay_archived')"
        )

    with pytest.raises(EventRetentionError, match="schema mismatch"):
        apply_event_retention(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM runtime_events").fetchone()[0] == 1


def test_malformed_timestamp_fails_closed_without_deleting_rows(tmp_path: Path) -> None:
    path = _db(tmp_path)
    _insert(path, "not-a-timestamp", 1)
    _insert(path, "2000-01-01 00:00:00", 1)

    with pytest.raises(EventRetentionError, match="non-canonical created_at"):
        apply_event_retention(
            path,
            keep_hours=1,
            keep_latest=0,
            evaluated_at=datetime(2026, 10, 7, 4, 0, tzinfo=timezone.utc),
        )

    assert _ids(path) == [1, 2]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("keep_hours", 1.5),
        ("keep_hours", True),
        ("keep_hours", 0),
        ("keep_latest", 1.5),
        ("keep_latest", True),
        ("keep_latest", -1),
        ("max_delete", 1.5),
        ("max_delete", True),
        ("max_delete", 0),
    ],
)
def test_retention_rejects_non_integer_or_out_of_range_limits(
    tmp_path: Path, field: str, value: object
) -> None:
    path = _db(tmp_path)
    kwargs: dict[str, object] = {
        "keep_hours": 24,
        "keep_latest": 10,
        "max_delete": 100,
    }
    kwargs[field] = value

    with pytest.raises(EventRetentionError, match="must be an integer"):
        apply_event_retention(path, **kwargs)  # type: ignore[arg-type]


def test_naive_evaluation_time_is_rejected(tmp_path: Path) -> None:
    path = _db(tmp_path)

    with pytest.raises(EventRetentionError, match="timezone-aware"):
        apply_event_retention(path, evaluated_at=datetime(2026, 10, 7, 4, 0))


def test_cli_emits_machine_readable_bounded_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _db(tmp_path)
    _insert(path, "2000-01-01 00:00:00", 3)

    code = main(
        [
            str(path),
            "--keep-hours",
            "1",
            "--keep-latest",
            "0",
            "--max-delete",
            "2",
            "--dry-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["ok"] is True
    assert payload["schema_version"] == "haxlab-runtime-event-retention-v1"
    assert payload["dry_run"] is True
    assert payload["max_delete"] == 2
    assert payload["rows_eligible"] == 3
    assert payload["rows_selected"] == 2
    assert payload["rows_deleted"] == 0
    assert payload["rows_remaining_eligible"] == 1
