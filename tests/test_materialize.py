from __future__ import annotations

import json
from pathlib import Path

import haxlab.learning.materialize as materialize_module
from haxlab.learning.materialize import materialize


def test_materialize_builds_train_then_holdout_and_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "out"
    calls: list[str] = []

    def fake_build_shards(**kwargs):
        split = kwargs["split"]
        calls.append(split)
        if split == "train":
            return {
                "analysis_version": "state-pass-v4",
                "requested_replays": 10,
                "successful_replays": 10,
                "failed_replays": 0,
                "samples": 1000,
                "compressed_bytes": 5000,
                "selected_players_seen": 20,
                "unknown_input_samples_skipped": 0,
            }
        return {
            "analysis_version": "state-pass-v4",
            "requested_replays": 2,
            "successful_replays": 2,
            "failed_replays": 0,
            "samples": 200,
            "compressed_bytes": 900,
            "selected_players_seen": 4,
            "unknown_input_samples_skipped": 0,
        }

    monkeypatch.setattr(materialize_module, "build_shards", fake_build_shards)

    summary = materialize(
        manifest_path=manifest,
        output_root=output,
        node_script=tmp_path / "extract.js",
        sample_every_ticks=6,
        workers=4,
    )

    assert calls == ["train", "holdout"]
    assert summary["failed_replays"] == 0
    assert summary["samples"] == 1200
    assert summary["compressed_bytes"] == 5900

    saved = json.loads(
        (output / "_complete.json").read_text(encoding="utf-8")
    )
    assert saved["schema"] == "haxlab-imitation-materialization-v1"
    assert saved["train"]["successful_replays"] == 10
    assert saved["holdout"]["successful_replays"] == 2


def test_materialize_propagates_failure_count(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")

    def fake_build_shards(**kwargs):
        failed = 1 if kwargs["split"] == "holdout" else 2
        return {
            "analysis_version": "state-pass-v4",
            "requested_replays": 5,
            "successful_replays": 5 - failed,
            "failed_replays": failed,
            "samples": 100,
            "compressed_bytes": 500,
            "selected_players_seen": 5,
            "unknown_input_samples_skipped": 0,
        }

    monkeypatch.setattr(materialize_module, "build_shards", fake_build_shards)

    summary = materialize(
        manifest_path=manifest,
        output_root=tmp_path / "out",
        node_script=tmp_path / "extract.js",
    )

    assert summary["failed_replays"] == 3
