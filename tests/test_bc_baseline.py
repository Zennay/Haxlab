from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np

from haxlab.learning.baseline import (
    direction_class,
    direction_from_class,
    train_baseline,
)


COLUMNS = [
    "frame",
    "player_index",
    "team_id",
    "own_x",
    "own_y",
    "own_vx",
    "own_vy",
    "ball_dx",
    "ball_dy",
    "ball_dvx",
    "ball_dvy",
    "tm1_dx",
    "tm1_dy",
    "tm1_dvx",
    "tm1_dvy",
    "tm1_present",
    "tm2_dx",
    "tm2_dy",
    "tm2_dvx",
    "tm2_dvy",
    "tm2_present",
    "op1_dx",
    "op1_dy",
    "op1_dvx",
    "op1_dvy",
    "op1_present",
    "op2_dx",
    "op2_dy",
    "op2_dvx",
    "op2_dvy",
    "op2_present",
    "op3_dx",
    "op3_dy",
    "op3_dvx",
    "op3_dvy",
    "op3_present",
    "dir_x",
    "dir_y",
    "kick",
]


def _write_shard(root: Path, name: str, rows: np.ndarray) -> dict:
    shard = root / f"{name}.f32.gz"
    meta = root / f"{name}.meta.json"
    root.mkdir(parents=True, exist_ok=True)

    with gzip.open(shard, "wb") as handle:
        handle.write(rows.astype("<f4").tobytes())

    meta.write_text(
        json.dumps(
            {
                "schema": "haxlab-imitation-extract-summary-v2",
                "rowWidth": len(COLUMNS),
                "columns": COLUMNS,
                "sampleEveryTicks": 6,
                "samples": int(rows.shape[0]),
            }
        ),
        encoding="utf-8",
    )
    return {
        "replay_sha256": name,
        "shard_path": str(shard),
        "samples": int(rows.shape[0]),
    }


def _synthetic_rows(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rows = np.zeros((n, len(COLUMNS)), dtype=np.float32)

    own_x = rng.normal(0, 20, n).astype(np.float32)
    ball_dx = rng.normal(0, 50, n).astype(np.float32)
    ball_dy = rng.normal(0, 35, n).astype(np.float32)

    rows[:, COLUMNS.index("frame")] = np.arange(n)
    rows[:, COLUMNS.index("player_index")] = rng.integers(0, 4, n)
    rows[:, COLUMNS.index("team_id")] = 1
    rows[:, COLUMNS.index("own_x")] = own_x
    rows[:, COLUMNS.index("ball_dx")] = ball_dx
    rows[:, COLUMNS.index("ball_dy")] = ball_dy
    rows[:, COLUMNS.index("tm1_present")] = 1
    rows[:, COLUMNS.index("op1_present")] = 1

    rows[:, COLUMNS.index("dir_x")] = np.where(
        ball_dx > 5,
        1,
        np.where(ball_dx < -5, -1, 0),
    )
    rows[:, COLUMNS.index("dir_y")] = np.where(
        ball_dy > 8,
        1,
        np.where(ball_dy < -8, -1, 0),
    )
    rows[:, COLUMNS.index("kick")] = (
        np.sqrt(ball_dx**2 + ball_dy**2) < 14
    ).astype(np.float32)
    return rows


def _write_index(path: Path, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-imitation-shard-index-v2",
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )


def test_direction_mapping_round_trips() -> None:
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            class_id = direction_class(dx, dy)
            assert 0 <= class_id < 9
            assert direction_from_class(class_id) == (dx, dy)


def test_baseline_trains_and_writes_holdout_metrics(tmp_path: Path) -> None:
    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"

    train_entries = [
        _write_shard(train_dir, "train-a", _synthetic_rows(1800, 1)),
        _write_shard(train_dir, "train-b", _synthetic_rows(1800, 2)),
    ]
    holdout_entries = [
        _write_shard(holdout_dir, "holdout-a", _synthetic_rows(1200, 3))
    ]

    train_index = tmp_path / "train-index.json"
    holdout_index = tmp_path / "holdout-index.json"
    _write_index(train_index, train_entries)
    _write_index(holdout_index, holdout_entries)

    output = tmp_path / "model"
    result = train_baseline(
        train_index_path=train_index,
        holdout_index_path=holdout_index,
        output_dir=output,
        hidden_dim=24,
        epochs=6,
        batch_size=256,
        learning_rate=0.005,
        seed=7,
    )

    metrics = result["final_holdout"]
    assert result["schema"] == "haxlab-bc-baseline-v1"
    assert (output / "model.npz").exists()
    assert (output / "metrics.json").exists()
    assert len(result["artifact_hashes"]["model_sha256"]) == 64
    assert len(result["artifact_hashes"]["train_index_sha256"]) == 64
    assert len(result["artifact_hashes"]["holdout_index_sha256"]) == 64
    assert metrics["samples"] == 1200
    assert 0.0 <= metrics["direction_accuracy"] <= 1.0
    assert 0.0 <= metrics["direction_macro_recall"] <= 1.0
    assert len(metrics["direction_recall_by_class"]) == 9
    assert len(metrics["direction_confusion"]) == 9
    assert all(len(row) == 9 for row in metrics["direction_confusion"])
    assert 0.0 <= metrics["kick_f1"] <= 1.0
    assert metrics["direction_accuracy"] > metrics["baselines"][
        "majority_direction_accuracy"
    ]


def test_example_weights_change_training_when_enabled(tmp_path: Path) -> None:
    train_dir = tmp_path / "train-weighted"
    holdout_dir = tmp_path / "holdout-weighted"

    a = _write_shard(train_dir, "train-a", _synthetic_rows(800, 11))
    b_rows = _synthetic_rows(800, 12)
    # Flip the horizontal target in the second shard so weighting has a
    # measurable effect on the learned decision boundary.
    dx_i = COLUMNS.index("dir_x")
    b_rows[:, dx_i] *= -1
    b = _write_shard(train_dir, "train-b", b_rows)
    a["example_weight"] = 2.0
    b["example_weight"] = 0.25

    holdout = _write_shard(
        holdout_dir,
        "holdout-a",
        _synthetic_rows(500, 13),
    )

    train_index = tmp_path / "train-weighted-index.json"
    holdout_index = tmp_path / "holdout-weighted-index.json"
    _write_index(train_index, [a, b])
    _write_index(holdout_index, [holdout])

    plain_dir = tmp_path / "plain-model"
    weighted_dir = tmp_path / "weighted-model"

    plain = train_baseline(
        train_index_path=train_index,
        holdout_index_path=holdout_index,
        output_dir=plain_dir,
        hidden_dim=16,
        epochs=2,
        batch_size=128,
        learning_rate=0.003,
        seed=19,
        use_example_weights=False,
    )
    weighted = train_baseline(
        train_index_path=train_index,
        holdout_index_path=holdout_index,
        output_dir=weighted_dir,
        hidden_dim=16,
        epochs=2,
        batch_size=128,
        learning_rate=0.003,
        seed=19,
        use_example_weights=True,
    )

    assert plain["training"]["use_example_weights"] is False
    assert weighted["training"]["use_example_weights"] is True

    with np.load(plain_dir / "model.npz") as plain_model:
        plain_wd = plain_model["wd"].copy()
    with np.load(weighted_dir / "model.npz") as weighted_model:
        weighted_wd = weighted_model["wd"].copy()

    assert not np.allclose(plain_wd, weighted_wd)


def test_calibration_split_sets_threshold_before_final_holdout(
    tmp_path: Path,
) -> None:
    train_dir = tmp_path / "train-cal"
    calibration_dir = tmp_path / "calibration"
    holdout_dir = tmp_path / "holdout-cal"

    train_entries = [
        _write_shard(train_dir, "train-a", _synthetic_rows(1200, 31)),
        _write_shard(train_dir, "train-b", _synthetic_rows(1200, 32)),
    ]
    calibration_entries = [
        _write_shard(
            calibration_dir,
            "cal-a",
            _synthetic_rows(600, 33),
        )
    ]
    holdout_entries = [
        _write_shard(
            holdout_dir,
            "holdout-a",
            _synthetic_rows(600, 34),
        )
    ]

    train_index = tmp_path / "train-cal-index.json"
    calibration_index = tmp_path / "cal-index.json"
    holdout_index = tmp_path / "holdout-cal-index.json"
    _write_index(train_index, train_entries)
    _write_index(calibration_index, calibration_entries)
    _write_index(holdout_index, holdout_entries)

    output = tmp_path / "calibrated-model"
    result = train_baseline(
        train_index_path=train_index,
        calibration_index_path=calibration_index,
        holdout_index_path=holdout_index,
        output_dir=output,
        hidden_dim=20,
        epochs=3,
        batch_size=256,
        learning_rate=0.004,
        seed=23,
    )

    assert result["calibration"] is not None
    assert result["calibration"]["samples"] == 600.0
    assert 0.0 <= result["kick_threshold"] <= 1.0
    assert result["final_holdout"]["kick_threshold"] == result["kick_threshold"]
    assert result["final_holdout"]["samples"] == 600
    assert len(result["artifact_hashes"]["calibration_index_sha256"]) == 64
    assert result["training"]["calibration_replays"] == 1

    for epoch in result["history"]:
        assert "calibration" in epoch
        assert "holdout" not in epoch
