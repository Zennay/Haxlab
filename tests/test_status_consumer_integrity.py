from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

from haxlab.runtime.state import RuntimeState
from haxlab.runtime.status import main


def _snapshot() -> dict[str, object]:
    return {
        "duration_seconds_probed": 3600.0,
        "probe_rate_per_minute_5m": 2.0,
        "processing_pending": 2,
        "raw_unique_replays": 10,
        "processing_ok": 7,
        "processing_failed": 1,
        "analysis_ok": 5,
        "analysis_failed": 1,
        "analysis_pending": 1,
        "analysis_rate_per_minute_5m": 1.0,
    }


def _run_with_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: dict[str, object],
) -> int:
    monkeypatch.setattr(
        RuntimeState,
        "status_snapshot",
        lambda self: dict(snapshot),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["haxlab-status", "--state-db", str(tmp_path / "state.sqlite3")],
    )
    return main()


def test_status_consumer_accepts_canonical_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_with_snapshot(tmp_path, monkeypatch, _snapshot()) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["duration_hours_probed"] == 1.0
    assert payload["processing_progress_percent"] == 70.0
    assert payload["analysis_progress_percent"] == 50.0
    assert payload["estimated_probe_minutes_remaining"] == 1.0
    assert payload["estimated_analysis_minutes_remaining"] == 1.0


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("raw_unique_replays", -1),
        ("raw_unique_replays", True),
        ("processing_ok", 1.5),
        ("processing_failed", -1),
        ("processing_pending", "2"),
        ("analysis_ok", False),
        ("analysis_failed", -1),
        ("analysis_pending", 1.0),
        ("duration_seconds_probed", math.nan),
        ("duration_seconds_probed", math.inf),
        ("duration_seconds_probed", -0.1),
        ("duration_seconds_probed", "3600"),
        ("probe_rate_per_minute_5m", math.nan),
        ("probe_rate_per_minute_5m", -0.1),
        ("probe_rate_per_minute_5m", True),
        ("analysis_rate_per_minute_5m", math.inf),
        ("analysis_rate_per_minute_5m", -0.1),
        ("analysis_rate_per_minute_5m", "1.0"),
        ("duration_seconds_probed", 10**400),
        ("probe_rate_per_minute_5m", 10**400),
        ("analysis_rate_per_minute_5m", 10**400),
    ],
)
def test_status_consumer_rejects_malformed_snapshot_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    value: object,
) -> None:
    snapshot = _snapshot()
    snapshot[field_name] = value

    with pytest.raises(
        ValueError,
        match=f"invalid_status_snapshot:{field_name}",
    ):
        _run_with_snapshot(tmp_path, monkeypatch, snapshot)


def test_status_consumer_rejects_processing_counts_above_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["processing_pending"] = 3

    with pytest.raises(
        ValueError,
        match="invalid_status_snapshot:processing_counts_exceed_raw",
    ):
        _run_with_snapshot(tmp_path, monkeypatch, snapshot)


def test_status_consumer_rejects_analysis_counts_above_processed_ok(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = _snapshot()
    snapshot["analysis_pending"] = 2

    with pytest.raises(
        ValueError,
        match="invalid_status_snapshot:analysis_counts_exceed_processing_ok",
    ):
        _run_with_snapshot(tmp_path, monkeypatch, snapshot)


@pytest.mark.parametrize(
    "field_name",
    [
        "raw_unique_replays",
        "processing_ok",
        "processing_failed",
        "processing_pending",
        "analysis_ok",
        "analysis_failed",
        "analysis_pending",
        "duration_seconds_probed",
        "probe_rate_per_minute_5m",
        "analysis_rate_per_minute_5m",
    ],
)
def test_status_consumer_rejects_missing_required_metrics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    snapshot = _snapshot()
    snapshot.pop(field_name)

    with pytest.raises(
        ValueError,
        match=f"invalid_status_snapshot:{field_name}",
    ):
        _run_with_snapshot(tmp_path, monkeypatch, snapshot)
