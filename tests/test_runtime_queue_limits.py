from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


@pytest.mark.parametrize("bad_limit", [True, False, 0, -1, 1.0, "1", None])
def test_unprocessed_queue_rejects_non_native_positive_limits(
    tmp_path: Path, bad_limit: object
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="native positive integer"):
            state.list_unprocessed_replays(limit=bad_limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_limit", [True, False, 0, -1, 1.0, "1", None])
def test_unanalyzed_queue_rejects_non_native_positive_limits(
    tmp_path: Path, bad_limit: object
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(ValueError, match="native positive integer"):
            state.list_unanalyzed_replays(limit=bad_limit)  # type: ignore[arg-type]


def test_unprocessed_queue_preserves_deterministic_order_and_boundary(
    tmp_path: Path,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        for sha in ("b" * 64, "a" * 64, "c" * 64):
            state.register_raw(
                sha256=sha,
                archive_path=str(tmp_path / f"{sha}.hbr2"),
                size_bytes=1,
            )

        rows = state.list_unprocessed_replays(limit=2)

    assert [row.sha256 for row in rows] == ["a" * 64, "b" * 64]


def test_unanalyzed_queue_preserves_retry_semantics_and_boundary(
    tmp_path: Path,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        shas = ("a" * 64, "b" * 64, "c" * 64)
        for sha in shas:
            state.register_raw(
                sha256=sha,
                archive_path=str(tmp_path / f"{sha}.hbr2"),
                size_bytes=1,
            )
            state.mark_replay_processing(sha256=sha, status="ok")

        state.mark_replay_analysis(
            sha256=shas[0],
            status="ok",
            analyzer_version=CURRENT_ANALYZER_VERSION,
        )
        state.mark_replay_analysis(
            sha256=shas[1],
            status="retry",
            analyzer_version=CURRENT_ANALYZER_VERSION,
        )

        rows = state.list_unanalyzed_replays(limit=1)

    assert [row.sha256 for row in rows] == ["b" * 64]
