from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import haxlab.runtime.analyzer as analyzer_module
from haxlab.runtime.analyzer import ANALYZER_VERSION, analyze_batch
from haxlab.runtime.state import RuntimeState


REPLAY_BYTES = b"durable analyzer replay"
REPLAY_SHA = hashlib.sha256(REPLAY_BYTES).hexdigest()


def _register_processed(state: RuntimeState, tmp_path: Path) -> None:
    replay = tmp_path / "raw" / f"{REPLAY_SHA}.hbr2"
    replay.parent.mkdir(parents=True, exist_ok=True)
    replay.write_bytes(REPLAY_BYTES)
    state.register_raw(
        sha256=REPLAY_SHA,
        archive_path=str(replay),
        size_bytes=len(REPLAY_BYTES),
    )
    state.mark_replay_processing(
        sha256=REPLAY_SHA,
        status="ok",
        format_version=3,
        total_frames=600,
        duration_seconds=10.0,
        decompressed_bytes=10,
    )


def _payload() -> dict[str, object]:
    return {
        "schemaVersion": 4,
        "totalFrames": 600,
        "rawEventCount": 2,
        "simulation": {
            "framesAdvanced": 600,
            "sampledStateCount": 100,
            "sampleEveryTicks": 6,
        },
        "players": [],
    }


def _row(state: RuntimeState):
    return state.connection.execute(
        """
        SELECT status, output_path, error
        FROM replay_analysis_versions
        WHERE sha256 = ? AND analyzer_version = ?
        """,
        (REPLAY_SHA, ANALYZER_VERSION),
    ).fetchone()


def _run(state: RuntimeState, tmp_path: Path) -> dict[str, int]:
    return analyze_batch(
        state,
        decoder_script=tmp_path / "decode_replay.js",
        derived_root=tmp_path / "derived",
        batch_size=1,
        workers=1,
    )


def _fake_decoder(command, **_kwargs):
    assert Path(command[2]).read_bytes() == REPLAY_BYTES
    return subprocess.CompletedProcess(
        command,
        0,
        stdout=json.dumps(_payload()),
        stderr="",
    )


def test_success_fsyncs_output_directory_before_ok_ledger_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analyzer_module.subprocess, "run", _fake_decoder)
    real_fsync = analyzer_module.os.fsync
    directory_synced = False
    original_mark = RuntimeState.mark_replay_analysis

    def tracking_fsync(fd: int) -> None:
        nonlocal directory_synced
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            directory_synced = True
        real_fsync(fd)

    def guarded_mark(self, *args, **kwargs):
        if kwargs.get("status") == "ok":
            assert directory_synced, "ledger ok must follow derived-directory fsync"
        return original_mark(self, *args, **kwargs)

    monkeypatch.setattr(analyzer_module.os, "fsync", tracking_fsync)
    monkeypatch.setattr(RuntimeState, "mark_replay_analysis", guarded_mark)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_processed(state, tmp_path)
        result = _run(state, tmp_path)
        row = _row(state)

    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert directory_synced is True
    assert row["status"] == "ok"
    assert Path(row["output_path"]).is_file()
    assert row["error"] is None


def test_directory_fsync_failure_cannot_record_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(analyzer_module.subprocess, "run", _fake_decoder)
    real_fsync = analyzer_module.os.fsync

    def failing_directory_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("simulated directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(analyzer_module.os, "fsync", failing_directory_fsync)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_processed(state, tmp_path)
        result = _run(state, tmp_path)
        row = _row(state)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["output_path"] is None
    assert str(row["error"]).startswith(
        "derived_output_publish_error:simulated directory fsync failure"
    )


@pytest.mark.parametrize("missing_flag", ["O_DIRECTORY", "O_NOFOLLOW"])
def test_missing_secure_directory_flags_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_flag: str,
) -> None:
    monkeypatch.setattr(analyzer_module.subprocess, "run", _fake_decoder)
    monkeypatch.delattr(analyzer_module.os, missing_flag, raising=False)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        _register_processed(state, tmp_path)
        result = _run(state, tmp_path)
        row = _row(state)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert row["status"] == "failed"
    assert row["output_path"] is None
    assert "derived_output_directory_sync_unsupported" in str(row["error"])
