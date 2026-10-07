from __future__ import annotations

import gzip
import json
import struct
import sys
from pathlib import Path

import pytest

from haxlab.learning import shard_audit


SHA = "a" * 64


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _fixture(tmp_path: Path, *, samples: int = 2) -> Path:
    shard_dir = tmp_path / "train"
    shard_dir.mkdir()
    shard_path = shard_dir / f"{SHA}.f32.gz"
    row = struct.pack(
        "<" + "f" * shard_audit.EXPECTED_ROW_WIDTH,
        *([0.0] * shard_audit.EXPECTED_ROW_WIDTH),
    )
    with gzip.open(shard_path, "wb") as handle:
        handle.write(row * samples)

    selected_players = {
        "0": "name:alpha",
        "1": "name:beta",
    }
    selected_replay_players = {
        "7": "name:alpha",
        "8": "name:beta",
    }
    selected_rows = [
        {
            "replay_player_id": 7,
            "identity": "name:alpha",
            "samples": 100,
        },
        {
            "replay_player_id": 8,
            "identity": "name:beta",
            "samples": 90,
        },
    ]
    meta = {
        "schema": shard_audit.META_SCHEMA,
        "shardSchema": shard_audit.SHARD_SCHEMA,
        "format": "float32-le-gzip",
        "dtype": "float32-le",
        "rowWidth": shard_audit.EXPECTED_ROW_WIDTH,
        "columns": list(shard_audit.EXPECTED_COLUMNS),
        "canonicalAttackDirection": "+x",
        "selectedPlayers": selected_players,
        "selectedReplayPlayers": selected_replay_players,
        "sourceFile": f"{SHA}.hbr2",
        "outputPath": str(shard_path),
        "totalFrames": 120,
        "framesAdvanced": 119,
        "sampleEveryTicks": 6,
        "samples": samples,
        "selectedStateSamples": samples + 1,
        "skippedUnknownInput": 1,
        "selectedPlayersRequested": 2,
        "selectedPlayersSeen": 2,
        "compressedBytes": shard_path.stat().st_size,
        "replay_sha256": SHA,
        "raw_path": f"/raw/{SHA}.hbr2",
        "shard_path": str(shard_path),
        "selected_player_ids": ["name:alpha", "name:beta"],
        "selected_players": selected_rows,
        "example_weight": 1.0,
        "status": "ok",
    }
    _write_json(shard_dir / f"{SHA}.meta.json", meta)

    index = {
        "schema": shard_audit.INDEX_SCHEMA,
        "generated_at": "2026-10-07T06:00:00+00:00",
        "manifest_path": "/derived/training/manifest.json",
        "manifest_schema": shard_audit.MANIFEST_SCHEMA,
        "analysis_version": "state-pass-v4",
        "split": "train",
        "sample_every_ticks": 6,
        "requested_replays": 1,
        "successful_replays": 1,
        "failed_replays": 0,
        "samples": samples,
        "compressed_bytes": shard_path.stat().st_size,
        "selected_players_seen": 2,
        "unknown_input_samples_skipped": 1,
        "entries": [dict(meta)],
        "failures": [],
    }
    _write_json(shard_dir / "_index.json", index)
    return shard_dir


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_audit_accepts_complete_shard_directory(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is True
    assert receipt["audited_shards"] == 1
    assert receipt["samples"] == 2
    assert receipt["uncompressed_bytes"] == (
        2 * shard_audit.EXPECTED_ROW_WIDTH * 4
    )
    assert len(receipt["index_sha256"]) == 64
    assert len(receipt["inventory_sha256"]) == 64
    assert receipt["errors"] == []


def test_audit_rejects_truncated_gzip(tmp_path: Path) -> None:
    shard_dir = _fixture(tmp_path)
    shard_path = shard_dir / f"{SHA}.f32.gz"
    payload = shard_path.read_bytes()
    shard_path.write_bytes(payload[:-8])

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        "invalid_gzip" in error
        or "uncompressed_size_mismatch" in error
        for error in receipt["errors"]
    )


def test_audit_rejects_sample_count_byte_length_drift(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    meta_path = shard_dir / f"{SHA}.meta.json"
    index_path = shard_dir / "_index.json"
    meta = _load(meta_path)
    index = _load(index_path)

    meta["samples"] = 3
    meta["selectedStateSamples"] = 4
    index["entries"][0]["samples"] = 3
    index["entries"][0]["selectedStateSamples"] = 4
    index["samples"] = 3
    _write_json(meta_path, meta)
    _write_json(index_path, index)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        "uncompressed_size_mismatch" in error
        for error in receipt["errors"]
    )


def test_audit_rejects_index_metadata_drift(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    index = _load(index_path)
    index["entries"][0]["example_weight"] = 1.5
    _write_json(index_path, index)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert (
        f"{SHA}:index_meta_mismatch:example_weight"
        in receipt["errors"]
    )


def test_audit_rejects_orphan_and_temporary_artifacts(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    (shard_dir / f"{'b' * 64}.f32.gz").write_bytes(b"orphan")
    (shard_dir / f"{SHA}.f32.gz.tmp-1234").write_bytes(
        b"partial"
    )

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert (
        f"orphan_artifact:{'b' * 64}.f32.gz"
        in receipt["errors"]
    )
    assert (
        f"orphan_artifact:{SHA}.f32.gz.tmp-1234"
        in receipt["errors"]
    )


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"),
    reason="symlinks unsupported",
)
def test_audit_rejects_symlinked_metadata(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    meta_path = shard_dir / f"{SHA}.meta.json"
    target = tmp_path / "external-meta.json"
    target.write_bytes(meta_path.read_bytes())
    meta_path.unlink()
    try:
        meta_path.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        f"{SHA}:meta:unsafe_or_unreadable_file" in error
        or f"symlink_artifact:{SHA}.meta.json" in error
        for error in receipt["errors"]
    )


def test_audit_rejects_duplicate_success_identity(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    index = _load(index_path)
    index["entries"].append(dict(index["entries"][0]))
    index["requested_replays"] = 2
    index["successful_replays"] = 2
    _write_json(index_path, index)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert f"{SHA}:duplicate_index_entry" in receipt["errors"]


def test_audit_rejects_selected_player_mapping_drift(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    meta_path = shard_dir / f"{SHA}.meta.json"
    index_path = shard_dir / "_index.json"
    meta = _load(meta_path)
    index = _load(index_path)

    meta["selected_players"][1]["identity"] = "name:other"
    index["entries"][0]["selected_players"][1][
        "identity"
    ] = "name:other"
    _write_json(meta_path, meta)
    _write_json(index_path, index)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert f"{SHA}:selected_players_mapping" in receipt["errors"]


def test_audit_detects_index_change_during_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    original = shard_audit._inspect_gzip_shard

    def mutating_inspect(*args, **kwargs):
        result = original(*args, **kwargs)
        index = _load(index_path)
        index["generated_at"] = (
            "2026-10-07T06:01:00+00:00"
        )
        _write_json(index_path, index)
        return result

    monkeypatch.setattr(
        shard_audit,
        "_inspect_gzip_shard",
        mutating_inspect,
    )

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert "index_changed_during_audit" in receipt["errors"]


def test_cli_returns_machine_readable_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-shard-audit",
            "--shard-dir",
            str(missing),
        ],
    )

    result = shard_audit.main()
    payload = json.loads(capsys.readouterr().out)

    assert result == 1
    assert payload["schema"] == shard_audit.AUDIT_SCHEMA
    assert payload["clean"] is False
    assert payload["errors"]


@pytest.mark.skipif(
    not hasattr(Path, "symlink_to"),
    reason="symlinks unsupported",
)
def test_audit_rejects_symlinked_shard_root(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "target"
    fixture_root.mkdir()
    target = _fixture(fixture_root)
    link = tmp_path / "linked-train"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(
        shard_audit.AuditInputError,
        match="shard_root_must_be_regular_directory",
    ):
        shard_audit.audit_shard_directory(link)


def test_audit_rejects_index_aggregate_count_drift(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    index = _load(index_path)
    index["compressed_bytes"] += 1
    index["selected_players_seen"] += 1
    _write_json(index_path, index)

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        error.startswith("compressed_bytes_mismatch:")
        for error in receipt["errors"]
    )
    assert any(
        error.startswith("selected_players_seen_mismatch:")
        for error in receipt["errors"]
    )


def test_audit_rejects_duplicate_index_json_keys(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    text = index_path.read_text(encoding="utf-8")
    text = text.replace(
        '"split": "train"',
        '"split": "train",\n  "split": "train"',
        1,
    )
    index_path.write_text(text, encoding="utf-8")

    with pytest.raises(
        shard_audit.AuditInputError,
        match="duplicate_json_key:split",
    ):
        shard_audit.audit_shard_directory(shard_dir)


def test_audit_rejects_duplicate_metadata_json_keys(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    meta_path = shard_dir / f"{SHA}.meta.json"
    text = meta_path.read_text(encoding="utf-8")
    text = text.replace(
        '"samples": 2',
        '"samples": 2,\n  "samples": 2',
        1,
    )
    meta_path.write_text(text, encoding="utf-8")

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        f"{SHA}:meta:duplicate_json_key:samples" == error
        for error in receipt["errors"]
    )


@pytest.mark.parametrize(
    "constant",
    ["NaN", "Infinity", "-Infinity"],
)
def test_audit_rejects_nonstandard_index_numeric_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    shard_dir = _fixture(tmp_path)
    index_path = shard_dir / "_index.json"
    text = index_path.read_text(encoding="utf-8")
    text = text.replace(
        '"sample_every_ticks": 6',
        f'"sample_every_ticks": {constant}',
        1,
    )
    index_path.write_text(text, encoding="utf-8")

    with pytest.raises(
        shard_audit.AuditInputError,
        match=f"invalid_json_constant:{constant}",
    ):
        shard_audit.audit_shard_directory(shard_dir)


def test_audit_rejects_nonstandard_metadata_numeric_constant(
    tmp_path: Path,
) -> None:
    shard_dir = _fixture(tmp_path)
    meta_path = shard_dir / f"{SHA}.meta.json"
    text = meta_path.read_text(encoding="utf-8")
    text = text.replace(
        '"example_weight": 1.0',
        '"example_weight": Infinity',
        1,
    )
    meta_path.write_text(text, encoding="utf-8")

    receipt = shard_audit.audit_shard_directory(shard_dir)

    assert receipt["clean"] is False
    assert any(
        f"{SHA}:meta:invalid_json_constant:Infinity" == error
        for error in receipt["errors"]
    )
