from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.runtime.analyzer as analyzer
from haxlab.runtime.state import RawReplayRecord


def _payload(
    *,
    sample_every_ticks: int = 6,
    total_frames: int = 600,
    frames_advanced: int = 600,
) -> dict[str, object]:
    return {
        "schemaVersion": 4,
        "featureVersion": "touch-chain-v1",
        "totalFrames": total_frames,
        "rawEventCount": 50,
        "simulation": {
            "sampleEveryTicks": sample_every_ticks,
            "sampledStateCount": 100,
            "framesAdvanced": frames_advanced,
        },
        "players": [],
    }


def _replay(tmp_path: Path, sha: str = "a" * 64) -> RawReplayRecord:
    raw = tmp_path / f"{sha}.hbr2"
    raw.write_bytes(b"raw")
    return RawReplayRecord(
        sha256=sha,
        archive_path=str(raw),
        size_bytes=raw.stat().st_size,
    )


def _output_path(tmp_path: Path, replay: RawReplayRecord) -> Path:
    return (
        tmp_path
        / analyzer.ANALYZER_VERSION
        / replay.sha256[:2]
        / replay.sha256[2:4]
        / f"{replay.sha256}.json"
    )


def test_analyze_one_reuses_only_valid_matching_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay(tmp_path)
    output = _output_path(tmp_path / "derived", replay)
    output.parent.mkdir(parents=True)
    output.write_text(json.dumps(_payload()), encoding="utf-8")

    def fail_run(*args, **kwargs):
        raise AssertionError("decoder must not run for a valid cache")

    monkeypatch.setattr(analyzer.subprocess, "run", fail_run)

    returned_replay, payload, error, returned_path = analyzer._analyze_one(
        replay,
        decoder_script=tmp_path / "tools" / "decode_replay.js",
        derived_root=tmp_path / "derived",
        sample_every_ticks=6,
        timeout_seconds=60,
    )

    assert returned_replay == replay
    assert error is None
    assert payload == _payload()
    assert returned_path == output


def test_analyze_one_redecodes_wrong_cadence_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay(tmp_path, "b" * 64)
    output = _output_path(tmp_path / "derived", replay)
    output.parent.mkdir(parents=True)
    output.write_text(
        json.dumps(_payload(sample_every_ticks=12)),
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    class Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps(_payload(sample_every_ticks=6))

    def fake_run(command, **kwargs):
        calls.append(command)
        return Completed()

    monkeypatch.setattr(analyzer.subprocess, "run", fake_run)

    _, payload, error, returned_path = analyzer._analyze_one(
        replay,
        decoder_script=tmp_path / "tools" / "decode_replay.js",
        derived_root=tmp_path / "derived",
        sample_every_ticks=6,
        timeout_seconds=60,
    )

    assert len(calls) == 1
    assert error is None
    assert payload == _payload(sample_every_ticks=6)
    assert returned_path == output
    assert json.loads(output.read_text(encoding="utf-8")) == payload


def test_analyze_one_rejects_non_object_decoder_payload_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay(tmp_path, "c" * 64)

    class Completed:
        returncode = 0
        stderr = ""
        stdout = "[]"

    monkeypatch.setattr(analyzer.subprocess, "run", lambda *a, **k: Completed())

    _, payload, error, returned_path = analyzer._analyze_one(
        replay,
        decoder_script=tmp_path / "tools" / "decode_replay.js",
        derived_root=tmp_path / "derived",
        sample_every_ticks=6,
        timeout_seconds=60,
    )

    assert payload is None
    assert error == "decoder_payload_invalid:payload_not_object"
    assert returned_path is None
    assert not _output_path(tmp_path / "derived", replay).exists()


def test_analysis_payload_contract_rejects_incomplete_or_wrong_feature() -> None:
    payload = _payload(total_frames=600, frames_advanced=500)
    payload["featureVersion"] = "old-feature"

    reasons = analyzer._validate_analysis_payload(
        payload,
        sample_every_ticks=6,
    )

    assert any(reason.startswith("feature_version_mismatch:") for reason in reasons)
    assert "incomplete_state_reconstruction:500/600" in reasons
