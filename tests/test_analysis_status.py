from pathlib import Path

from haxlab.runtime.state import RuntimeState


def test_analysis_pending_tracks_probed_replays(tmp_path: Path) -> None:
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

        before = state.status_snapshot()
        state.mark_replay_analysis(
            sha256="a" * 64,
            status="ok",
            sampled_state_count=100,
            player_count=8,
            raw_event_count=50,
            tick_count=600,
        )
        after = state.status_snapshot()

    assert before["analysis_pending"] == 1
    assert after["analysis_pending"] == 0
    assert after["analysis_ok"] == 1
    assert after["analysis_sampled_states"] == 100
    assert after["analysis_ticks_reconstructed"] == 600
