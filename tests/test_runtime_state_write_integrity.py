from pathlib import Path

import pytest

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


def _count(state: RuntimeState, table: str) -> int:
    row = state.connection.execute(
        f"SELECT COUNT(*) AS count FROM {table}"
    ).fetchone()
    return int(row["count"])


def _register_raw(state: RuntimeState, tmp_path: Path, sha: str) -> None:
    state.register_raw(
        sha256=sha,
        archive_path=str(tmp_path / f"{sha}.hbr2"),
        size_bytes=1,
    )


@pytest.mark.parametrize(
    "bad_sha",
    [
        None,
        True,
        1,
        b"a" * 64,
        "",
        "a" * 63,
        "A" * 64,
        "g" * 64,
        "a" * 65,
    ],
)
def test_register_raw_rejects_noncanonical_sha_without_writing(
    tmp_path: Path,
    bad_sha: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="canonical lowercase SHA-256"):
            state.register_raw(
                sha256=bad_sha,  # type: ignore[arg-type]
                archive_path=str(tmp_path / "raw.hbr2"),
                size_bytes=1,
            )
        assert _count(state, "raw_replays") == 0


@pytest.mark.parametrize("bad_size", [True, -1, 1.0, "1"])
def test_register_raw_rejects_non_native_nonnegative_size(
    tmp_path: Path,
    bad_size: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="native non-negative integer"):
            state.register_raw(
                sha256="a" * 64,
                archive_path=str(tmp_path / "raw.hbr2"),
                size_bytes=bad_size,  # type: ignore[arg-type]
            )
        assert _count(state, "raw_replays") == 0


@pytest.mark.parametrize("bad_path", [None, True, "", " ", " /tmp/replay.hbr2"])
def test_register_raw_rejects_malformed_archive_path(
    tmp_path: Path,
    bad_path: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="archive_path must be"):
            state.register_raw(
                sha256="a" * 64,
                archive_path=bad_path,  # type: ignore[arg-type]
                size_bytes=1,
            )
        assert _count(state, "raw_replays") == 0


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("size_bytes", True),
        ("size_bytes", -1),
        ("size_bytes", 1.0),
        ("mtime_ns", True),
        ("mtime_ns", -1),
        ("mtime_ns", "1"),
    ],
)
def test_mark_seen_rejects_invalid_native_integer_evidence_before_write(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    kwargs: dict[str, object] = {
        "source_path": str(tmp_path / "source.hbr2"),
        "size_bytes": 1,
        "mtime_ns": 2,
        "sha256": "b" * 64,
        "status": "archived",
    }
    kwargs[field] = bad_value

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="native non-negative integer"):
            state.mark_seen(**kwargs)  # type: ignore[arg-type]
        assert _count(state, "source_files") == 0


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("format_version", True),
        ("format_version", -1),
        ("format_version", 3.0),
        ("total_frames", True),
        ("total_frames", -1),
        ("total_frames", "10"),
        ("decompressed_bytes", True),
        ("decompressed_bytes", -1),
        ("decompressed_bytes", 4.0),
    ],
)
def test_processing_write_rejects_invalid_integer_metrics_without_row(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    sha = "c" * 64
    kwargs: dict[str, object] = {
        "sha256": sha,
        "status": "ok",
        "format_version": 3,
        "total_frames": 10,
        "duration_seconds": 1.5,
        "decompressed_bytes": 20,
        "parser_stage": "probe",
    }
    kwargs[field] = bad_value

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        with pytest.raises(ValueError, match="native non-negative integer"):
            state.mark_replay_processing(**kwargs)  # type: ignore[arg-type]
        assert _count(state, "replay_processing") == 0


@pytest.mark.parametrize(
    "bad_duration",
    [
        True,
        "1.5",
        -0.1,
        float("nan"),
        float("inf"),
        float("-inf"),
        10**10000,
    ],
)
def test_processing_write_rejects_invalid_duration_without_row(
    tmp_path: Path,
    bad_duration: object,
) -> None:
    sha = "d" * 64
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        with pytest.raises(
            ValueError,
            match="native finite non-negative number",
        ):
            state.mark_replay_processing(
                sha256=sha,
                status="ok",
                duration_seconds=bad_duration,  # type: ignore[arg-type]
            )
        assert _count(state, "replay_processing") == 0


@pytest.mark.parametrize("bad_stage", [None, True, "", " ", " probe", "probe "])
def test_processing_write_rejects_malformed_parser_stage_without_row(
    tmp_path: Path,
    bad_stage: object,
) -> None:
    sha = "e" * 64
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        with pytest.raises(ValueError, match="parser_stage must be"):
            state.mark_replay_processing(
                sha256=sha,
                status="ok",
                parser_stage=bad_stage,  # type: ignore[arg-type]
            )
        assert _count(state, "replay_processing") == 0


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("sampled_state_count", True),
        ("sampled_state_count", -1),
        ("sampled_state_count", 1.0),
        ("player_count", True),
        ("player_count", -1),
        ("player_count", "2"),
        ("raw_event_count", True),
        ("raw_event_count", -1),
        ("raw_event_count", 3.0),
        ("tick_count", True),
        ("tick_count", -1),
        ("tick_count", "4"),
    ],
)
def test_analysis_write_rejects_invalid_count_evidence_without_row(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    sha = "f" * 64
    kwargs: dict[str, object] = {
        "sha256": sha,
        "status": "ok",
        "sampled_state_count": 1,
        "player_count": 2,
        "raw_event_count": 3,
        "tick_count": 4,
    }
    kwargs[field] = bad_value

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        with pytest.raises(ValueError, match="native non-negative integer"):
            state.mark_replay_analysis(**kwargs)  # type: ignore[arg-type]
        assert _count(state, "replay_analysis_versions") == 0


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("analyzer_version", ""),
        ("analyzer_version", " "),
        ("analyzer_version", True),
        ("output_path", ""),
        ("output_path", " "),
        ("output_path", 1),
        ("error", True),
        ("error", 1),
    ],
)
def test_analysis_write_rejects_malformed_text_evidence_without_row(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    sha = "1" * 64
    kwargs: dict[str, object] = {
        "sha256": sha,
        "status": "ok",
        "analyzer_version": CURRENT_ANALYZER_VERSION,
        "output_path": str(tmp_path / "derived.json"),
        "error": None,
    }
    kwargs[field] = bad_value

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        with pytest.raises(ValueError):
            state.mark_replay_analysis(**kwargs)  # type: ignore[arg-type]
        assert _count(state, "replay_analysis_versions") == 0


def test_valid_runtime_write_evidence_round_trips_unchanged(tmp_path: Path) -> None:
    sha = "2" * 64
    archive_path = str(tmp_path / "raw.hbr2")
    source_path = str(tmp_path / "incoming.hbr2")
    output_path = str(tmp_path / "derived.json")

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.mark_seen(
            source_path=source_path,
            size_bytes=123,
            mtime_ns=456,
            sha256=sha,
            status="archived",
        )
        state.register_raw(
            sha256=sha,
            archive_path=archive_path,
            size_bytes=123,
        )
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            format_version=3,
            total_frames=600,
            duration_seconds=10.0,
            decompressed_bytes=4096,
            parser_stage="probe",
        )
        state.mark_replay_analysis(
            sha256=sha,
            status="ok",
            analyzer_version=CURRENT_ANALYZER_VERSION,
            output_path=output_path,
            sampled_state_count=100,
            player_count=8,
            raw_event_count=12,
            tick_count=600,
        )

        raw = dict(
            state.connection.execute(
                "SELECT sha256, archive_path, size_bytes FROM raw_replays"
            ).fetchone()
        )
        processing = dict(
            state.connection.execute(
                """
                SELECT format_version, total_frames, duration_seconds,
                       decompressed_bytes, parser_stage
                FROM replay_processing
                """
            ).fetchone()
        )
        analysis = dict(
            state.connection.execute(
                """
                SELECT analyzer_version, output_path, sampled_state_count,
                       player_count, raw_event_count, tick_count
                FROM replay_analysis_versions
                """
            ).fetchone()
        )

    assert raw == {
        "sha256": sha,
        "archive_path": archive_path,
        "size_bytes": 123,
    }
    assert processing == {
        "format_version": 3,
        "total_frames": 600,
        "duration_seconds": 10.0,
        "decompressed_bytes": 4096,
        "parser_stage": "probe",
    }
    assert analysis == {
        "analyzer_version": CURRENT_ANALYZER_VERSION,
        "output_path": output_path,
        "sampled_state_count": 100,
        "player_count": 8,
        "raw_event_count": 12,
        "tick_count": 600,
    }
