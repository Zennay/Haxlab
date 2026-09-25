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


def test_analysis_versions_are_preserved(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    replay = tmp_path / "r.hbr2"
    replay.write_bytes(b"x")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256="b" * 64,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256="b" * 64,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256="b" * 64,
            analyzer_version="state-pass-v3",
            status="ok",
            sampled_state_count=100,
            player_count=8,
            raw_event_count=50,
            tick_count=600,
        )
        state.mark_replay_analysis(
            sha256="b" * 64,
            analyzer_version="state-pass-v4",
            status="failed",
            error="example",
        )

        rows = state.connection.execute(
            """
            SELECT analyzer_version, status
            FROM replay_analysis_versions
            WHERE sha256 = ?
            ORDER BY analyzer_version
            """,
            ("b" * 64,),
        ).fetchall()
        snapshot = state.status_snapshot()

    assert [(row["analyzer_version"], row["status"]) for row in rows] == [
        ("state-pass-v3", "ok"),
        ("state-pass-v4", "failed"),
    ]
    assert snapshot["analysis_versions"]["state-pass-v3"]["ok"] == 1
    assert snapshot["analysis_versions"]["state-pass-v4"]["failed"] == 1


def test_recent_analysis_rate_uses_elapsed_window(tmp_path: Path) -> None:
    db = tmp_path / "state.sqlite3"
    replay = tmp_path / "rate.hbr2"
    replay.write_bytes(b"x")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256="c" * 64,
            archive_path=str(replay),
            size_bytes=1,
        )
        state.mark_replay_processing(
            sha256="c" * 64,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=10,
        )
        state.mark_replay_analysis(
            sha256="c" * 64,
            status="ok",
            sampled_state_count=100,
            player_count=6,
            raw_event_count=50,
            tick_count=600,
        )
        state.connection.execute(
            """
            UPDATE replay_analysis_versions
            SET updated_at = datetime('now', '-60 seconds')
            WHERE sha256 = ?
            """,
            ("c" * 64,),
        )
        state.connection.commit()

        snapshot = state.status_snapshot()

    rate = float(snapshot["analysis_rate_per_minute_5m"])
    assert 0.95 <= rate <= 1.05
