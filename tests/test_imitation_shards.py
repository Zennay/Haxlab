from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.learning.shards as shards
from haxlab.learning.selector import MANIFEST_SCHEMA


def test_extract_one_uses_cached_valid_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha = "a" * 64
    output_dir = tmp_path / "train"
    output_dir.mkdir()
    shard = output_dir / f"{sha}.f32.gz"
    meta = output_dir / f"{sha}.meta.json"
    shard.write_bytes(b"cached")
    meta.write_text(
        json.dumps(
            {
                "schema": "haxlab-imitation-extract-summary-v2",
                "sampleEveryTicks": 6,
                "samples": 123,
                "compressedBytes": 6,
                "replay_sha256": sha,
            }
        ),
        encoding="utf-8",
    )

    def fail_run(*args, **kwargs):
        raise AssertionError("subprocess should not run for a valid cache")

    monkeypatch.setattr(shards.subprocess, "run", fail_run)

    result = shards._extract_one(
        {
            "replay_sha256": sha,
            "raw_path": str(tmp_path / "missing.hbr2"),
            "selected_player_ids": ["name:alpha"],
            "selected_players": [
                {
                    "replay_player_id": 7,
                    "identity": "name:alpha",
                    "samples": 100,
                }
            ],
        },
        node_script=tmp_path / "tools" / "extract_imitation.js",
        output_dir=output_dir,
        sample_every_ticks=6,
        timeout_seconds=60,
        force=False,
    )

    assert result["status"] == "cached"
    assert result["samples"] == 123


def test_build_shards_rejects_wrong_manifest_schema(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema": "wrong", "train_replays": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported manifest schema"):
        shards.build_shards(
            manifest_path=manifest,
            split="train",
            output_root=tmp_path / "out",
            node_script=tmp_path / "extract.js",
        )


def test_build_shards_honors_split_and_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train = [
        {
            "replay_sha256": char * 64,
            "raw_path": str(tmp_path / f"{char}.hbr2"),
            "selected_player_ids": [f"name:{char}"],
            "selected_players": [
                {
                    "replay_player_id": 1,
                    "identity": f"name:{char}",
                    "samples": 100,
                }
            ],
            "example_weight": 1.0,
        }
        for char in ("a", "b", "c")
    ]
    holdout = [
        {
            "replay_sha256": "d" * 64,
            "raw_path": str(tmp_path / "d.hbr2"),
            "selected_player_ids": ["name:d"],
            "selected_players": [
                {
                    "replay_player_id": 1,
                    "identity": "name:d",
                    "samples": 100,
                }
            ],
            "example_weight": 1.0,
        }
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "analysis_version": "state-pass-v4",
                "train_replays": train,
                "holdout_replays": holdout,
            }
        ),
        encoding="utf-8",
    )

    seen: list[str] = []

    def fake_extract(entry, **kwargs):
        sha = str(entry["replay_sha256"])
        seen.append(sha)
        return {
            "schema": "haxlab-imitation-extract-summary-v2",
            "replay_sha256": sha,
            "samples": 100,
            "compressedBytes": 1000,
            "selectedPlayersSeen": 1,
            "skippedUnknownInput": 5,
            "status": "ok",
        }

    monkeypatch.setattr(shards, "_extract_one", fake_extract)

    index = shards.build_shards(
        manifest_path=manifest,
        split="train",
        output_root=tmp_path / "out",
        node_script=tmp_path / "extract.js",
        workers=1,
        limit=2,
    )

    assert index["requested_replays"] == 2
    assert index["successful_replays"] == 2
    assert index["failed_replays"] == 0
    assert index["samples"] == 200
    assert index["compressed_bytes"] == 2000
    assert index["selected_players_seen"] == 2
    assert index["unknown_input_samples_skipped"] == 10
    assert seen == ["a" * 64, "b" * 64]

    saved = json.loads(
        (tmp_path / "out" / "train" / "_index.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["split"] == "train"
    assert len(saved["entries"]) == 2
