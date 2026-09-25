from __future__ import annotations

import json
from pathlib import Path

from haxlab.runtime.finalize import finalize_analysis_if_ready
from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


def test_finalize_requires_complete_analysis(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
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

        result = finalize_analysis_if_ready(state, derived_root=derived)

    assert result["status"] == "not_ready"
    assert not (derived / CURRENT_ANALYZER_VERSION / "_complete.json").exists()


def test_finalize_writes_versioned_snapshot_and_manifest(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"
    replay = tmp_path / "r.hbr2"
    replay.write_bytes(b"x")
    sha = "b" * 64

    analysis_root = derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
    analysis_root.mkdir(parents=True)
    (analysis_root / f"{sha}.json").write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 600,
                "simulation": {"sampleEveryTicks": 6},
                "players": [],
            }
        ),
        encoding="utf-8",
    )

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
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256=sha,
            analyzer_version=CURRENT_ANALYZER_VERSION,
            status="ok",
            output_path=str(analysis_root / f"{sha}.json"),
            sampled_state_count=100,
            player_count=0,
            raw_event_count=50,
            tick_count=600,
        )

        first = finalize_analysis_if_ready(state, derived_root=derived)
        second = finalize_analysis_if_ready(state, derived_root=derived)

    leaderboard = (
        derived / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    )
    completion = derived / CURRENT_ANALYZER_VERSION / "_complete.json"

    assert first["status"] == "finalized"
    assert second["status"] == "already_finalized"
    assert leaderboard.exists()
    assert completion.exists()

    leaderboard_payload = json.loads(leaderboard.read_text(encoding="utf-8"))
    completion_payload = json.loads(completion.read_text(encoding="utf-8"))

    assert leaderboard_payload["analysis_version"] == CURRENT_ANALYZER_VERSION
    assert leaderboard_payload["rows"] == []
    assert completion_payload["analysis_ok"] == 1
    assert completion_payload["analysis_failed"] == 0
    assert completion_payload["analysis_pending"] == 0
    assert completion_payload["analysis_ticks_reconstructed"] == 600


def test_finalize_refreshes_when_dataset_grows(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    derived = tmp_path / "derived"

    with RuntimeState(db) as state:
        for index, char in enumerate(("c", "d"), start=1):
            sha = char * 64
            replay = tmp_path / f"{char}.hbr2"
            replay.write_bytes(b"x")

            analysis_root = (
                derived / CURRENT_ANALYZER_VERSION / sha[:2] / sha[2:4]
            )
            analysis_root.mkdir(parents=True, exist_ok=True)
            output = analysis_root / f"{sha}.json"
            output.write_text(
                json.dumps(
                    {
                        "schemaVersion": 4,
                        "totalFrames": 600,
                        "simulation": {"sampleEveryTicks": 6},
                        "players": [],
                    }
                ),
                encoding="utf-8",
            )

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
                decompressed_bytes=10,
            )
            state.mark_replay_analysis(
                sha256=sha,
                analyzer_version=CURRENT_ANALYZER_VERSION,
                status="ok",
                output_path=str(output),
                sampled_state_count=100,
                player_count=0,
                raw_event_count=50,
                tick_count=600,
            )

            result = finalize_analysis_if_ready(state, derived_root=derived)
            assert result["status"] == "finalized"

            completion = json.loads(
                (
                    derived
                    / CURRENT_ANALYZER_VERSION
                    / "_complete.json"
                ).read_text(encoding="utf-8")
            )
            assert completion["raw_unique_replays"] == index
            assert completion["analysis_ok"] == index
            assert completion["analysis_ticks_reconstructed"] == index * 600
