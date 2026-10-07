from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

import haxlab.runtime.analyzer as analyzer_module
from haxlab.runtime.analyzer import ANALYZER_VERSION, analyze_batch
from haxlab.runtime.state import RuntimeState


def _payload() -> dict[str, object]:
    return {
        "schemaVersion": 4,
        "totalFrames": 600,
        "rawEventCount": 3,
        "simulation": {
            "framesAdvanced": 600,
            "sampledStateCount": 100,
            "sampleEveryTicks": 6,
        },
        "players": [],
    }


def _register(
    state: RuntimeState,
    tmp_path: Path,
    content: bytes,
) -> tuple[Path, str]:
    sha256 = hashlib.sha256(content).hexdigest()
    archive = tmp_path / "raw" / f"{sha256}.hbr2"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(content)
    state.register_raw(
        sha256=sha256,
        archive_path=str(archive),
        size_bytes=len(content),
    )
    state.mark_replay_processing(
        sha256=sha256,
        status="ok",
        format_version=3,
        total_frames=600,
        duration_seconds=10.0,
        decompressed_bytes=10,
    )
    return archive, sha256


def _row(state: RuntimeState, sha256: str):
    return state.connection.execute(
        """
        SELECT status, output_path, error
        FROM replay_analysis_versions
        WHERE sha256 = ? AND analyzer_version = ?
        """,
        (sha256, ANALYZER_VERSION),
    ).fetchone()


def _run_one(
    state: RuntimeState,
    tmp_path: Path,
) -> dict[str, int]:
    return analyze_batch(
        state,
        decoder_script=tmp_path / "decode_replay.js",
        derived_root=tmp_path / "derived",
        batch_size=1,
        workers=1,
    )


def test_analyzer_decodes_only_verified_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"verified analyzer source bytes"
    seen: list[Path] = []

    def fake_run(command, **_kwargs):
        decode_input = Path(command[2])
        assert decode_input.read_bytes() == content
        seen.append(decode_input)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(_payload()),
            stderr="",
        )

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        archive, sha256 = _register(state, tmp_path, content)
        result = _run_one(state, tmp_path)
        row = _row(state, sha256)

    assert result == {"selected": 1, "ok": 1, "failed": 0}
    assert len(seen) == 1
    assert seen[0] != archive
    assert not seen[0].exists()
    assert row["status"] == "ok"
    assert row["error"] is None


def test_analyzer_rejects_same_size_archive_hash_tamper_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = b"0123456789abcdef"
    tampered = b"fedcba9876543210"
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("decoder must not run for tampered archive")

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        archive, sha256 = _register(state, tmp_path, original)
        archive.write_bytes(tampered)
        result = _run_one(state, tmp_path)
        row = _row(state, sha256)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert called is False
    assert row["status"] == "failed"
    assert row["output_path"] is None
    assert str(row["error"]).startswith(
        "archive_integrity_error:archive_sha256_mismatch:"
    )


def test_analyzer_rejects_archive_size_drift_before_decode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("decoder must not run for size drift")

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        archive, sha256 = _register(state, tmp_path, b"stable")
        archive.write_bytes(b"stable-but-longer")
        result = _run_one(state, tmp_path)
        row = _row(state, sha256)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert called is False
    assert row["status"] == "failed"
    assert str(row["error"]).startswith(
        "archive_integrity_error:archive_size_mismatch:"
    )


def test_analyzer_rejects_archive_symlink_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("decoder must not run for symlink archive")

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        archive, sha256 = _register(state, tmp_path, b"stable symlink source")
        target = tmp_path / "replacement.hbr2"
        target.write_bytes(b"stable symlink source")
        archive.unlink()
        archive.symlink_to(target)
        result = _run_one(state, tmp_path)
        row = _row(state, sha256)

    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert called is False
    assert row["status"] == "failed"
    assert row["error"] == "archive_integrity_error:archive_symlink_not_allowed"


def test_analyzer_rejects_archive_mutation_during_verified_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = b"x" * (1024 * 1024 + 32)
    called = False
    mutated = False
    real_read = os.read
    archive_ref: Path | None = None

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("decoder must not run for racy archive")

    def mutating_read(fd: int, count: int) -> bytes:
        nonlocal mutated
        chunk = real_read(fd, count)
        if chunk and not mutated and archive_ref is not None:
            mutated = True
            archive_ref.write_bytes(b"y" * len(content))
        return chunk

    monkeypatch.setattr(analyzer_module.subprocess, "run", fake_run)
    monkeypatch.setattr(analyzer_module.os, "read", mutating_read)

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        archive, sha256 = _register(state, tmp_path, content)
        archive_ref = archive
        result = _run_one(state, tmp_path)
        row = _row(state, sha256)

    assert mutated is True
    assert result == {"selected": 1, "ok": 0, "failed": 1}
    assert called is False
    assert row["status"] == "failed"
    assert str(row["error"]).startswith("archive_integrity_error:archive_")
