import sys
from pathlib import Path

import pytest

from haxlab.runtime import analyzer as analyzer_module
from haxlab.runtime import worker as worker_module
from haxlab.runtime.analyzer import analyze_batch
from haxlab.runtime.state import RuntimeState


@pytest.mark.parametrize("raw_batch_size", ["0", "-1"])
def test_worker_main_rejects_invalid_batch_size_before_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_batch_size: str,
) -> None:
    state_path = tmp_path / "state.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-worker",
            "--state-db",
            str(state_path),
            "--batch-size",
            raw_batch_size,
            "--once",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        worker_module.main()

    assert exc_info.value.code == 2
    assert not state_path.exists()


@pytest.mark.parametrize("raw_batch_size", ["0", "-1"])
def test_analyzer_main_does_not_clamp_invalid_batch_size(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_batch_size: str,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-analyzer",
            "--state-db",
            str(tmp_path / "state.sqlite3"),
            "--derived-root",
            str(tmp_path / "derived"),
            "--decoder-script",
            str(tmp_path / "decode_replay.js"),
            "--batch-size",
            raw_batch_size,
            "--workers",
            "1",
            "--once",
        ],
    )

    with pytest.raises(
        ValueError,
        match="queue limit must be a native positive integer",
    ):
        analyzer_module.main()


@pytest.mark.parametrize("bad_batch_size", [0, -1, True, 1.0, "1", None])
def test_analyze_batch_preserves_strict_queue_limit_contract(
    tmp_path: Path,
    bad_batch_size: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(
            ValueError,
            match="queue limit must be a native positive integer",
        ):
            analyze_batch(
                state,
                decoder_script=tmp_path / "decode_replay.js",
                derived_root=tmp_path / "derived",
                batch_size=bad_batch_size,  # type: ignore[arg-type]
                workers=1,
            )


@pytest.mark.parametrize("bad_workers", [0, -1, True, 1.0, "1", None])
def test_analyze_batch_rejects_invalid_worker_count_before_queue_read(
    tmp_path: Path,
    bad_workers: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(
            ValueError,
            match="workers must be a native positive integer",
        ):
            analyze_batch(
                state,
                decoder_script=tmp_path / "decode_replay.js",
                derived_root=tmp_path / "derived",
                batch_size=1,
                workers=bad_workers,  # type: ignore[arg-type]
            )


def test_analyze_batch_accepts_positive_limits_on_empty_queue(tmp_path: Path) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        result = analyze_batch(
            state,
            decoder_script=tmp_path / "decode_replay.js",
            derived_root=tmp_path / "derived",
            batch_size=1,
            workers=1,
        )

    assert result == {"selected": 0, "ok": 0, "failed": 0}
