from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.learning.elite_shards as elite_shards
from haxlab.learning.elite_selector import ELITE_MANIFEST_SCHEMA


def test_elite_shard_spec_passes_role_and_skill_weight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw.hbr2"
    raw.write_bytes(b"HBR2fake")
    output_dir = tmp_path / "out"
    node_script = tmp_path / "extract_elite_imitation.js"
    node_script.write_text("// fake", encoding="utf-8")

    seen = {}

    class Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "schema": "haxlab-elite-imitation-extract-summary-v1",
                "sampleEveryTicks": 6,
                "samples": 100,
                "compressedBytes": 500,
                "selectedPlayersSeen": 1,
                "skippedUnknownInput": 0,
                "roleSampleCounts": {"dm": 100},
            }
        )

    def fake_run(command, **kwargs):
        seen["command"] = command
        shard = Path(command[3])
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_bytes(b"fake")
        return Completed()

    monkeypatch.setattr(elite_shards.subprocess, "run", fake_run)

    result = elite_shards._extract_one(
        {
            "replay_sha256": "a" * 64,
            "raw_path": str(raw),
            "selected_players": [
                {
                    "replay_player_id": 7,
                    "identity": "alias:sekai",
                    "role": "dm",
                    "skill_weight": 1.25,
                    "role_confidence": 0.8,
                }
            ],
        },
        node_script=node_script,
        output_dir=output_dir,
        sample_every_ticks=6,
        timeout_seconds=60,
        force=True,
    )

    spec = json.loads(seen["command"][4])
    assert spec["7"]["identity"] == "alias:sekai"
    assert spec["7"]["role"] == "dm"
    assert spec["7"]["weight"] == 1.0
    assert result["selected_player_spec_hash"]
    assert (
        result["role_confidence_weighting"]
        == "skill_weight_x_clamped_role_confidence"
    )
    assert result["samples"] == 100


def test_elite_shards_support_three_splits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    base_entry = {
        "raw_path": str(tmp_path / "raw.hbr2"),
        "selected_players": [{"replay_player_id": 1, "identity": "x", "role": "gk"}],
    }
    manifest.write_text(
        json.dumps(
            {
                "schema": ELITE_MANIFEST_SCHEMA,
                "train_replays": [{"replay_sha256": "a" * 64, **base_entry}],
                "validation_replays": [{"replay_sha256": "b" * 64, **base_entry}],
                "holdout_replays": [{"replay_sha256": "c" * 64, **base_entry}],
            }
        ),
        encoding="utf-8",
    )

    def fake_extract(entry, **kwargs):
        return {
            "replay_sha256": entry["replay_sha256"],
            "samples": 10,
            "compressedBytes": 10,
            "selectedPlayersSeen": 1,
            "skippedUnknownInput": 0,
            "roleSampleCounts": {"gk": 10},
        }

    monkeypatch.setattr(elite_shards, "_extract_one", fake_extract)

    for split, expected in (("train", "a"), ("validation", "b"), ("holdout", "c")):
        index = elite_shards.build_elite_shards(
            manifest_path=manifest,
            split=split,
            output_root=tmp_path / "shards",
            node_script=tmp_path / "extract.js",
            workers=1,
        )
        assert index["successful_replays"] == 1
        assert index["entries"][0]["replay_sha256"] == expected * 64


def test_elite_shard_cache_invalidates_when_player_spec_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = tmp_path / "raw.hbr2"
    raw.write_bytes(b"HBR2fake")
    output_dir = tmp_path / "out"
    node_script = tmp_path / "extract_elite_imitation.js"
    node_script.write_text("// fake", encoding="utf-8")

    calls = {"count": 0}

    class Completed:
        returncode = 0
        stderr = ""
        stdout = json.dumps(
            {
                "schema": "haxlab-elite-imitation-extract-summary-v1",
                "sampleEveryTicks": 6,
                "samples": 100,
                "compressedBytes": 500,
                "selectedPlayersSeen": 1,
                "skippedUnknownInput": 0,
                "roleSampleCounts": {"dm": 100},
            }
        )

    def fake_run(command, **kwargs):
        calls["count"] += 1
        shard = Path(command[3])
        shard.parent.mkdir(parents=True, exist_ok=True)
        shard.write_bytes(b"fake")
        return Completed()

    monkeypatch.setattr(elite_shards.subprocess, "run", fake_run)

    base = {
        "replay_sha256": "d" * 64,
        "raw_path": str(raw),
        "selected_players": [
            {
                "replay_player_id": 7,
                "identity": "alias:sekai",
                "role": "dm",
                "skill_weight": 1.25,
                "role_confidence": 0.8,
            }
        ],
    }

    first = elite_shards._extract_one(
        base,
        node_script=node_script,
        output_dir=output_dir,
        sample_every_ticks=6,
        timeout_seconds=60,
        force=False,
    )
    second = elite_shards._extract_one(
        base,
        node_script=node_script,
        output_dir=output_dir,
        sample_every_ticks=6,
        timeout_seconds=60,
        force=False,
    )
    assert calls["count"] == 1
    assert second["status"] == "cached"

    changed = {
        **base,
        "selected_players": [
            {
                **base["selected_players"][0],
                "role_confidence": 0.6,
            }
        ],
    }
    third = elite_shards._extract_one(
        changed,
        node_script=node_script,
        output_dir=output_dir,
        sample_every_ticks=6,
        timeout_seconds=60,
        force=False,
    )

    assert calls["count"] == 2
    assert third["status"] == "ok"
    assert third["selected_player_spec_hash"] != first["selected_player_spec_hash"]
