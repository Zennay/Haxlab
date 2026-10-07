from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

import haxlab.runtime.analyzer as analyzer_module
from haxlab.runtime.analyzer import ANALYZER_VERSION, analyze_batch
from haxlab.runtime.state import RuntimeState


REPLAY_BYTES = b"raw replay bytes"
REPLAY_SHA = hashlib.sha256(REPLAY_BYTES).hexdigest()


def _register_processed_replay(
    state: RuntimeState,
    tmp_path: Path,
    *,
    sha256: str,
) -> Path:
    replay = tmp_path / "raw" / f"{sha256}.hbr2"
    replay.parent.mkdir(parents=True, exist_ok=True)
    replay.write_bytes(REPLAY_BYTES)
    state.register_raw(
        sha256=sha256,
        archive_path=str(replay),
        size_bytes=replay.stat().st_size,
    )
    state.mark_replay_processing(
        sha256=sha256,
        status="ok",
        format_version=3,
        total_frames=600,
        duration_seconds=10.0,
        decompressed_bytes=10,
    )
    return replay


def _analysis_output(derived_root: Path, sha256: str) -> Path:
    return (
        derived_root
        / ANALYZER_VERSION
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.json"
    )


def _fresh_payload() -> dict[str, object]:
    return {
        "schemaVersion": 4,
        "totalFrames": 600,
        "rawEventCount": 7,
        "simulation": {
            "framesAdvanced": 600,
            "sampledStateCount": 100,
            "sampleEveryTicks": 6,
        },
        "players": [],
    }


def _analysis_row(state: RuntimeState, sha256: str):
    return state.connection.execute(
        """
        SELECT status, output_path, sampled_state_count, player_count,
               raw_event_count, tick_count, error
        FROM replay_analysis_versions
        WHERE sha256 = ? AND analyzer_version = ?
        """,
        (sha256, ANALYZER_VERSION),
    ).fetchone()


def test_pending_replay_recomputes_and_replaces_orphan_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = REPLAY_SHA
    derived_root = tmp_path / "derived"
    orphan_path = _analysis_output(derived_root, sha256)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_text(
        json.dumps(
            {
                "schemaVersion": 4,
                "totalFrames": 999,
                "rawEventCount": 999,
                "simulation": {
                    "framesAdvanced": 999,
                    "sampledStateCount": 999,
                },
                "players": [{"id": 1}],
            }
        ),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        assert Path(command[2]).read_bytes() == REPLAY_BYTES
        calls.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_fresh_payload()),
            stderr="",
        )

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        replay = _register_processed_replay(state, tmp_path, sha256=sha256)

        result = analyze_batch(
            state,
            decoder_script=tmp_path / "decode_replay.js",
            derived_root=derived_root,
            batch_size=1,
            workers=1,
        )
        row = _analysis_row(state, sha256)

    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert len(calls) == 1
    assert calls[0][2] != str(replay)
    assert not Path(calls[0][2]).exists()
    assert json.loads(orphan_path.read_text(encoding="utf-8")) == _fresh_payload()
    assert row["status"] == "ok"
    assert row["output_path"] == str(orphan_path)
    assert row["sampled_state_count"] == 100
    assert row["player_count"] == 0
    assert row["raw_event_count"] == 7
    assert row["tick_count"] == 600
    assert row["error"] is None


def test_retry_replay_does_not_promote_orphan_when_decoder_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = REPLAY_SHA
    derived_root = tmp_path / "derived"
    orphan_path = _analysis_output(derived_root, sha256)
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_payload = _fresh_payload()
    orphan_path.write_text(json.dumps(orphan_payload), encoding="utf-8")

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="",
            stderr="decoder refused replay",
        )

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_processed_replay(state, tmp_path, sha256=sha256)
        state.mark_replay_analysis(
            sha256=sha256,
            status="retry",
            analyzer_version=ANALYZER_VERSION,
            error="previous interrupted attempt",
        )

        result = analyze_batch(
            state,
            decoder_script=tmp_path / "decode_replay.js",
            derived_root=derived_root,
            batch_size=1,
            workers=1,
        )
        row = _analysis_row(state, sha256)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["output_path"] is None
    assert row["sampled_state_count"] is None
    assert row["raw_event_count"] is None
    assert str(row["error"]).startswith("decoder_exit_1:")
    assert json.loads(orphan_path.read_text(encoding="utf-8")) == orphan_payload


def test_pending_replay_without_orphan_preserves_healthy_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = REPLAY_SHA
    derived_root = tmp_path / "derived"

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_fresh_payload()),
            stderr="",
        )

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_processed_replay(state, tmp_path, sha256=sha256)

        result = analyze_batch(
            state,
            decoder_script=tmp_path / "decode_replay.js",
            derived_root=derived_root,
            batch_size=1,
            workers=1,
        )
        row = _analysis_row(state, sha256)

    output_path = _analysis_output(derived_root, sha256)
    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert output_path.exists()
    assert row["status"] == "ok"
    assert row["output_path"] == str(output_path)
