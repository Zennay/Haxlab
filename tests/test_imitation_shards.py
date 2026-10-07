from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.learning.shards as shards
from haxlab.learning.selector import MANIFEST_SCHEMA


def _write_manifest(path: Path, *, train_replays=None, holdout_replays=None) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": MANIFEST_SCHEMA,
                "analysis_version": "state-pass-v4",
                "train_replays": list(train_replays or []),
                "holdout_replays": list(holdout_replays or []),
            }
        ),
        encoding="utf-8",
    )


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_every_ticks", 0),
        ("sample_every_ticks", -1),
        ("sample_every_ticks", True),
        ("sample_every_ticks", 1.0),
        ("sample_every_ticks", "1"),
        ("workers", 0),
        ("workers", -1),
        ("workers", False),
        ("workers", 2.0),
        ("workers", "2"),
        ("timeout_seconds", 29),
        ("timeout_seconds", -1),
        ("timeout_seconds", True),
        ("timeout_seconds", 30.0),
        ("timeout_seconds", "30"),
        ("limit", -1),
        ("limit", False),
        ("limit", 1.0),
        ("limit", "1"),
    ],
)
def test_build_shards_rejects_malformed_numeric_config_before_publication(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest)
    output_root = tmp_path / "out"

    with pytest.raises(ValueError, match=field):
        shards.build_shards(
            manifest_path=manifest,
            split="train",
            output_root=output_root,
            node_script=tmp_path / "extract.js",
            **{field: value},
        )

    assert not output_root.exists()


def test_build_shards_preserves_valid_numeric_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "replay_sha256": "e" * 64,
        "raw_path": str(tmp_path / "e.hbr2"),
        "selected_players": [
            {
                "replay_player_id": 1,
                "identity": "name:e",
                "samples": 100,
            }
        ],
    }
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, train_replays=[entry])
    seen: list[tuple[int, int]] = []

    def fake_extract(entry, **kwargs):
        seen.append(
            (kwargs["sample_every_ticks"], kwargs["timeout_seconds"])
        )
        return {
            "schema": "haxlab-imitation-extract-summary-v2",
            "replay_sha256": str(entry["replay_sha256"]),
            "samples": 1,
            "compressedBytes": 1,
            "selectedPlayersSeen": 1,
            "skippedUnknownInput": 0,
            "status": "ok",
        }

    monkeypatch.setattr(shards, "_extract_one", fake_extract)

    index = shards.build_shards(
        manifest_path=manifest,
        split="train",
        output_root=tmp_path / "out",
        node_script=tmp_path / "extract.js",
        sample_every_ticks=7,
        workers=1,
        limit=1,
        timeout_seconds=31,
    )

    assert seen == [(7, 31)]
    assert index["sample_every_ticks"] == 7
    assert index["requested_replays"] == 1


def test_build_shards_accepts_zero_limit(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        train_replays=[
            {
                "replay_sha256": "f" * 64,
                "raw_path": str(tmp_path / "f.hbr2"),
                "selected_players": [],
            }
        ],
    )

    index = shards.build_shards(
        manifest_path=manifest,
        split="train",
        output_root=tmp_path / "out",
        node_script=tmp_path / "extract.js",
        workers=1,
        limit=0,
        timeout_seconds=30,
    )

    assert index["requested_replays"] == 0
    assert index["failed_replays"] == 0


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
    _write_manifest(
        manifest,
        train_replays=train,
        holdout_replays=holdout,
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
