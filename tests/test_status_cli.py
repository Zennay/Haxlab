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
