from __future__ import annotations

from pathlib import Path

import haxlab.learning.elite_pipeline as pipeline


def test_pipeline_uses_split_specific_replay_limits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    seen: dict[str, int | None] = {}

    monkeypatch.setattr(
        pipeline,
        "build_elite_manifest",
        lambda **kwargs: {
            "stats": {},
            "train_replays": [],
            "validation_replays": [],
            "holdout_replays": [],
        },
    )

    def fake_shards(*, split: str, limit: int | None, output_root: Path, **kwargs):
        seen[split] = limit
        split_dir = output_root / split
        split_dir.mkdir(parents=True, exist_ok=True)
        (split_dir / "_index.json").write_text("{}", encoding="utf-8")
        return {
            "failed_replays": 0,
            "samples": 10,
            "successful_replays": 1,
            "role_sample_counts": {},
        }

    monkeypatch.setattr(pipeline, "build_elite_shards", fake_shards)
    monkeypatch.setattr(
        pipeline,
        "train_elite_policy",
        lambda **kwargs: {
            "training": {
                "best_epoch": 1,
                "calibrated_kick_threshold": 0.3,
                "frozen_holdout_used_for_selection": False,
                "kick_threshold_source": "validation_only",
                "kick_calibration": {"constraint_satisfied": True},
            },
            "final_validation": {
                "direction_accuracy": 0.5,
                "kick_f1": 0.1,
            },
            "final_holdout": {
                "samples": 20000,
                "direction_accuracy": 0.5,
                "kick_f1": 0.1,
                "kick_true_rate": 0.04,
                "kick_predicted_rate": 0.04,
                "baselines": {"majority_direction_accuracy": 0.2},
                "by_role": {
                    role: {
                        "direction_accuracy": 0.5,
                        "baselines": {"majority_direction_accuracy": 0.2},
                    }
                    for role in ("gk", "dm", "am", "st")
                },
            },
        },
    )

    result = pipeline.run_elite_pipeline(
        analysis_root=tmp_path / "analysis",
        leaderboard_path=tmp_path / "leaderboard.json",
        raw_root=tmp_path / "raw",
        work_root=tmp_path / "work",
        node_script=tmp_path / "extract.js",
        replay_limit=50,
        train_replay_limit=800,
        validation_replay_limit=200,
        holdout_replay_limit=None,
    )

    assert seen == {
        "train": 800,
        "validation": 200,
        "holdout": 50,
    }
    assert result["splits"]["train"]["replays"] == 1
