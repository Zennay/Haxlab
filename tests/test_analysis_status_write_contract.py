from pathlib import Path

import pytest

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


def _register_raw(state: RuntimeState, tmp_path: Path, sha: str) -> None:
    state.register_raw(
        sha256=sha,
        archive_path=str(tmp_path / f"{sha}.hbr2"),
        size_bytes=1,
    )


def _analysis_rows(state: RuntimeState) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in state.connection.execute(
            """
            SELECT sha256, analyzer_version, status, error
            FROM replay_analysis_versions
            ORDER BY sha256, analyzer_version
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
        "FAILED",
    ],
)
def test_analysis_write_rejects_invalid_status_without_row(
    tmp_path: Path,
    bad_status: object,
) -> None:
    sha = "a" * 64
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)

        with pytest.raises(
            ValueError,
            match="analysis status must be one of: failed, ok, retry",
        ):
            state.mark_replay_analysis(
                sha256=sha,
                status=bad_status,  # type: ignore[arg-type]
            )

        assert _analysis_rows(state) == []


def test_analysis_write_rejects_invalid_update_without_clobbering_row(
    tmp_path: Path,
) -> None:
    sha = "b" * 64
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_analysis(
            sha256=sha,
            status="ok",
            error=None,
        )

        with pytest.raises(ValueError):
            state.mark_replay_analysis(
                sha256=sha,
                status="not-a-state",
                error="must not be persisted",
            )

        rows = _analysis_rows(state)

    assert rows == [
        {
            "sha256": sha,
            "analyzer_version": CURRENT_ANALYZER_VERSION,
            "status": "ok",
            "error": None,
        }
    ]


@pytest.mark.parametrize("status", ["ok", "failed", "retry"])
def test_analysis_write_accepts_canonical_statuses(
    tmp_path: Path,
    status: str,
) -> None:
    sha = {
        "ok": "c",
        "failed": "d",
        "retry": "e",
    }[status] * 64

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_analysis(
            sha256=sha,
            status=status,
            error="example" if status == "failed" else None,
        )
        rows = _analysis_rows(state)

    assert rows[0]["status"] == status


def test_retry_status_remains_queueable(tmp_path: Path) -> None:
    sha = "f" * 64
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_raw(state, tmp_path, sha)
        state.mark_replay_processing(sha256=sha, status="ok")
        state.mark_replay_analysis(sha256=sha, status="retry")

        queued = state.list_unanalyzed_replays(limit=10)

    assert [item.sha256 for item in queued] == [sha]
