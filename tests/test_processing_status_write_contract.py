from pathlib import Path

import pytest

from haxlab.runtime.state import RuntimeState


def _register_raw(state: RuntimeState, tmp_path: Path, sha: str) -> None:
    state.register_raw(
        sha256=sha,
        archive_path=str(tmp_path / f"{sha}.hbr2"),
        size_bytes=1,
    )


def _processing_rows(state: RuntimeState) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in state.connection.execute(
            """
            SELECT sha256, status, parser_stage, error
            FROM replay_processing
            ORDER BY sha256
            """
        )
    ]


@pytest.mark.parametrize(
    "bad_status",
    [
        None,
        True,
        1,
        1.0,
        b"ok",
        "",
        "unknown",
        " ok",
        "ok ",
        "OK",
        "retry",
    ],
)
def test_processing_write_rejects_invalid_status_without_row(
    tmp_path: Path,
    bad_status: object,
) -> None:
    sha = "a" * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)

        with pytest.raises(
            ValueError,
            match="processing status must be one of: failed, ok",
        ):
            state.mark_replay_processing(
                sha256=sha,
                status=bad_status,  # type: ignore[arg-type]
            )

        assert _processing_rows(state) == []


def test_processing_write_rejects_invalid_update_without_clobbering_row(
    tmp_path: Path,
) -> None:
    sha = "b" * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_processing(
            sha256=sha,
            status="ok",
            parser_stage="probe",
            error=None,
        )

        with pytest.raises(ValueError):
            state.mark_replay_processing(
                sha256=sha,
                status="not-a-state",
                parser_stage="corrupt",
                error="must not be persisted",
            )

        rows = _processing_rows(state)

    assert rows == [
        {
            "sha256": sha,
            "status": "ok",
            "parser_stage": "probe",
            "error": None,
        }
    ]


@pytest.mark.parametrize("status", ["ok", "failed"])
def test_processing_write_accepts_canonical_statuses(
    tmp_path: Path,
    status: str,
) -> None:
    sha = {"ok": "c", "failed": "d"}[status] * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_processing(
            sha256=sha,
            status=status,
            parser_stage="probe",
            error="example" if status == "failed" else None,
        )
        rows = _processing_rows(state)

    assert rows[0]["status"] == status


def test_only_successful_processing_is_analysis_queueable(
    tmp_path: Path,
) -> None:
    ok_sha = "e" * 64
    failed_sha = "f" * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        for sha in (ok_sha, failed_sha):
            _register_raw(state, tmp_path, sha)

        state.mark_replay_processing(sha256=ok_sha, status="ok")
        state.mark_replay_processing(sha256=failed_sha, status="failed")

        queued = state.list_unanalyzed_replays(limit=10)

    assert [item.sha256 for item in queued] == [ok_sha]


def test_failed_processing_is_not_reclassified_as_unprocessed(
    tmp_path: Path,
) -> None:
    sha = "1" * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_processing(sha256=sha, status="failed")

        unprocessed = state.list_unprocessed_replays(limit=10)

    assert unprocessed == []
