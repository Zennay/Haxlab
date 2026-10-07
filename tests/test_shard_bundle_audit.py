from __future__ import annotations

import gzip
import json
import struct
from pathlib import Path

import pytest

from haxlab.learning import shard_audit
from haxlab.learning import shard_bundle_audit


TRAIN_SHA = "a" * 64
HOLDOUT_SHA = "b" * 64


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_success(
    shard_dir: Path,
    *,
    replay_sha: str,
    split: str,
    analysis_version: str = "state-pass-v4",
    manifest_path: str = "/derived/training/manifest.json",
    sample_every_ticks: int = 6,
) -> None:
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard_path = shard_dir / f"{replay_sha}.f32.gz"
    row = struct.pack(
        "<" + "f" * shard_audit.EXPECTED_ROW_WIDTH,
        *([0.0] * shard_audit.EXPECTED_ROW_WIDTH),
    )
    with gzip.open(shard_path, "wb") as handle:
        handle.write(row * 2)

    selected_players = {"0": "name:alpha"}
    selected_replay_players = {"7": "name:alpha"}
    selected_rows = [
        {
            "replay_player_id": 7,
            "identity": "name:alpha",
            "samples": 2,
        }
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
        "sourceFile": f"{replay_sha}.hbr2",
        "outputPath": str(shard_path),
        "totalFrames": 120,
        "framesAdvanced": 119,
        "sampleEveryTicks": sample_every_ticks,
        "samples": 2,
        "selectedStateSamples": 2,
        "skippedUnknownInput": 0,
        "selectedPlayersRequested": 1,
        "selectedPlayersSeen": 1,
        "compressedBytes": shard_path.stat().st_size,
        "replay_sha256": replay_sha,
        "raw_path": f"/raw/{replay_sha}.hbr2",
        "shard_path": str(shard_path),
        "selected_player_ids": ["name:alpha"],
        "selected_players": selected_rows,
        "example_weight": 1.0,
        "status": "ok",
    }
    _write_json(
        shard_dir / f"{replay_sha}.meta.json",
        meta,
    )
    _write_json(
        shard_dir / "_index.json",
        {
            "schema": shard_audit.INDEX_SCHEMA,
            "generated_at": "2026-10-07T07:00:00+00:00",
            "manifest_path": manifest_path,
            "manifest_schema": shard_audit.MANIFEST_SCHEMA,
            "analysis_version": analysis_version,
            "split": split,
            "sample_every_ticks": sample_every_ticks,
            "requested_replays": 1,
            "successful_replays": 1,
            "failed_replays": 0,
            "samples": 2,
            "compressed_bytes": shard_path.stat().st_size,
            "selected_players_seen": 1,
            "unknown_input_samples_skipped": 0,
            "entries": [dict(meta)],
            "failures": [],
        },
    )


def _write_failure(
    shard_dir: Path,
    *,
    replay_sha: str,
    split: str,
    analysis_version: str = "state-pass-v4",
    manifest_path: str = "/derived/training/manifest.json",
    sample_every_ticks: int = 6,
) -> None:
    shard_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        shard_dir / "_index.json",
        {
            "schema": shard_audit.INDEX_SCHEMA,
            "generated_at": "2026-10-07T07:00:00+00:00",
            "manifest_path": manifest_path,
            "manifest_schema": shard_audit.MANIFEST_SCHEMA,
            "analysis_version": analysis_version,
            "split": split,
            "sample_every_ticks": sample_every_ticks,
            "requested_replays": 1,
            "successful_replays": 0,
            "failed_replays": 1,
            "samples": 0,
            "compressed_bytes": 0,
            "selected_players_seen": 0,
            "unknown_input_samples_skipped": 0,
            "entries": [],
            "failures": [
                {
                    "replay_sha256": replay_sha,
                    "error": "decoder_failed",
                }
            ],
        },
    )


def _pair(tmp_path: Path) -> tuple[Path, Path]:
    train = tmp_path / "train"
    holdout = tmp_path / "holdout"
    _write_success(
        train,
        replay_sha=TRAIN_SHA,
        split="train",
    )
    _write_success(
        holdout,
        replay_sha=HOLDOUT_SHA,
        split="holdout",
    )
    return train, holdout


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_bundle_audit_accepts_clean_disjoint_pair(
    tmp_path: Path,
) -> None:
    train, holdout = _pair(tmp_path)

    first = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )
    second = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert first == second
    assert first["schema"] == shard_bundle_audit.AUDIT_SCHEMA
    assert first["clean"] is True
    assert first["analysis_version"] == "state-pass-v4"
    assert first["sample_every_ticks"] == 6
    assert first["unique_replays"] == 2
    assert len(first["inventory_sha256"]) == 64
    assert len(first["train"]["index_sha256"]) == 64
    assert len(first["holdout"]["index_sha256"]) == 64
    assert first["errors"] == []


def test_bundle_audit_rejects_split_label_drift(
    tmp_path: Path,
) -> None:
    train, holdout = _pair(tmp_path)
    index_path = holdout / "_index.json"
    index = _load(index_path)
    index["split"] = "train"
    _write_json(index_path, index)

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert "holdout:split:'train'" in receipt["errors"]


def test_bundle_audit_rejects_success_success_leakage(
    tmp_path: Path,
) -> None:
    train = tmp_path / "train"
    holdout = tmp_path / "holdout"
    _write_success(
        train,
        replay_sha=TRAIN_SHA,
        split="train",
    )
    _write_success(
        holdout,
        replay_sha=TRAIN_SHA,
        split="holdout",
    )

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert (
        f"cross_split_replay_leakage:{TRAIN_SHA}"
        in receipt["errors"]
    )


def test_bundle_audit_rejects_success_failure_leakage(
    tmp_path: Path,
) -> None:
    train = tmp_path / "train"
    holdout = tmp_path / "holdout"
    _write_success(
        train,
        replay_sha=TRAIN_SHA,
        split="train",
    )
    _write_failure(
        holdout,
        replay_sha=TRAIN_SHA,
        split="holdout",
    )

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert (
        f"cross_split_replay_leakage:{TRAIN_SHA}"
        in receipt["errors"]
    )


@pytest.mark.parametrize(
    ("field", "value", "error_prefix"),
    [
        (
            "analysis_version",
            "state-pass-v5",
            "cross_split_analysis_version_mismatch:",
        ),
        (
            "manifest_path",
            "/derived/training/other.json",
            "cross_split_manifest_path_mismatch:",
        ),
        (
            "sample_every_ticks",
            8,
            "cross_split_sample_every_ticks_mismatch:",
        ),
    ],
)
def test_bundle_audit_rejects_cross_split_provenance_drift(
    tmp_path: Path,
    field: str,
    value: object,
    error_prefix: str,
) -> None:
    train, holdout = _pair(tmp_path)
    index_path = holdout / "_index.json"
    index = _load(index_path)
    index[field] = value

    if field == "sample_every_ticks":
        entry = index["entries"][0]
        entry["sampleEveryTicks"] = value
        meta_path = holdout / f"{HOLDOUT_SHA}.meta.json"
        meta = _load(meta_path)
        meta["sampleEveryTicks"] = value
        _write_json(meta_path, meta)

    _write_json(index_path, index)

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert any(
        error.startswith(error_prefix)
        for error in receipt["errors"]
    )


def test_bundle_audit_propagates_directory_corruption(
    tmp_path: Path,
) -> None:
    train, holdout = _pair(tmp_path)
    shard_path = train / f"{TRAIN_SHA}.f32.gz"
    payload = shard_path.read_bytes()
    shard_path.write_bytes(payload[:-8])

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert any(
        error.startswith("train:directory:")
        and (
            "invalid_gzip" in error
            or "uncompressed_size_mismatch" in error
        )
        for error in receipt["errors"]
    )


def test_bundle_audit_detects_index_change_during_pair_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train, holdout = _pair(tmp_path)
    original = shard_audit.audit_shard_directory
    calls = 0

    def mutating_audit(path: Path) -> dict:
        nonlocal calls
        receipt = original(path)
        calls += 1
        if calls == 2:
            index_path = train / "_index.json"
            index = _load(index_path)
            index["generated_at"] = (
                "2026-10-07T07:01:00+00:00"
            )
            _write_json(index_path, index)
        return receipt

    monkeypatch.setattr(
        shard_audit,
        "audit_shard_directory",
        mutating_audit,
    )

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert (
        "train:index_changed_during_bundle_audit"
        in receipt["errors"]
    )


def test_bundle_audit_rejects_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    train, holdout = _pair(tmp_path)
    index_path = holdout / "_index.json"
    text = index_path.read_text(encoding="utf-8")
    text = text.replace(
        '"split": "holdout"',
        '"split": "holdout",\n  "split": "holdout"',
        1,
    )
    index_path.write_text(text, encoding="utf-8")

    receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train,
        holdout_dir=holdout,
    )

    assert receipt["clean"] is False
    assert receipt["errors"] == [
        "duplicate_json_key:split"
    ]
