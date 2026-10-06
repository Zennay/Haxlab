from __future__ import annotations

import json
import sys
from pathlib import Path

from haxlab.runtime.state import RuntimeState
from haxlab.runtime.status import main


def test_status_reports_analysis_progress_and_completion(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    replay = tmp_path / "r.hbr2"
    replay.write_bytes(b"x")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256="a" * 64,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256="a" * 64,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256="a" * 64,
            status="ok",
            sampled_state_count=100,
            player_count=6,
            raw_event_count=50,
            tick_count=600,
        )

    monkeypatch.setattr(
        sys,
        "argv",
        ["haxlab-status", "--state-db", str(db)],
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["processing_progress_percent"] == 100.0
    assert payload["analysis_progress_percent"] == 100.0
    assert payload["analysis_complete"] is True


def test_status_surfaces_ingest_integrity_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    failed_source = tmp_path / "incoming" / "broken.hbr2"

    with RuntimeState(db) as state:
        state.mark_seen(
            source_path=str(failed_source),
            size_bytes=123,
            mtime_ns=456,
            sha256=None,
            status="failed",
            error="invalid_hbr2:bad_header",
        )
        state.event(
            "replay_failed",
            subject=str(failed_source),
            detail="invalid_hbr2:bad_header",
        )
        state.event(
            "replay_disappeared",
            subject=str(tmp_path / "incoming" / "moving.hbr2"),
            detail="disappeared_before_stat",
        )

    monkeypatch.setattr(
        sys,
        "argv",
        ["haxlab-status", "--state-db", str(db)],
    )
    assert main() == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["source_failed"] == 1
    assert payload["ingest_integrity_ok"] is False
    assert payload["recent_ingest_events_1h"]["replay_failed"] == 1
    assert payload["recent_ingest_events_1h"]["replay_disappeared"] == 1
    assert payload["recent_ingest_events_1h"]["replay_archived"] == 0
    assert payload["recent_ingest_events_1h"]["replay_duplicate"] == 0
    assert payload["source_failure_examples"] == [
        {
            "source_path": str(failed_source),
            "error": "invalid_hbr2:bad_header",
            "last_seen_at": payload["source_failure_examples"][0]["last_seen_at"],
        }
    ]
