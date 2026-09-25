from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from haxlab.learning.baseline import (
    _split_train_validation,
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
    assert result["schema"] == "haxlab-bc-baseline-v2"
    assert (output / "model.npz").exists()
    assert (output / "policy.json").exists()
    assert (output / "metrics.json").exists()
    assert metrics["samples"] == 1200
    assert result["training"]["train_replays_fit"] == 1
    assert result["training"]["validation_replays"] == 1
    assert 0.10 <= result["training"]["selected_kick_threshold"] <= 0.90
    assert 0.0 <= metrics["direction_accuracy"] <= 1.0
    assert 0.0 <= metrics["kick_f1"] <= 1.0
    assert metrics["direction_accuracy"] > metrics["baselines"][
        "majority_direction_accuracy"
    ]
    assert len(metrics["direction_confusion"]) == 9
    assert all(len(row) == 9 for row in metrics["direction_confusion"])
    assert 0.0 <= metrics["direction_macro_recall"] <= 1.0
    assert all("validation" in epoch for epoch in result["history"])
    assert all("holdout" not in epoch for epoch in result["history"])


def test_validation_split_is_deterministic() -> None:
    index = {
        "entries": [
            {"replay_sha256": f"{i:064x}"}
            for i in range(40)
        ]
    }

    fit_a, val_a = _split_train_validation(
        index,
        validation_fraction=0.20,
        seed=123,
    )
    fit_b, val_b = _split_train_validation(
        index,
        validation_fraction=0.20,
        seed=123,
    )

    assert [x["replay_sha256"] for x in fit_a["entries"]] == [
        x["replay_sha256"] for x in fit_b["entries"]
    ]
    assert [x["replay_sha256"] for x in val_a["entries"]] == [
        x["replay_sha256"] for x in val_b["entries"]
    ]
    assert fit_a["entries"]
    assert val_a["entries"]


def test_exported_policy_runs_in_node(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    train_dir = tmp_path / "train"
    holdout_dir = tmp_path / "holdout"
    train_entries = [
        _write_shard(train_dir, "train-a", _synthetic_rows(1000, 11)),
        _write_shard(train_dir, "train-b", _synthetic_rows(1000, 12)),
    ]
    holdout_entries = [
        _write_shard(holdout_dir, "holdout-a", _synthetic_rows(500, 13))
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
        hidden_dim=16,
        epochs=2,
        batch_size=256,
        learning_rate=0.005,
        seed=17,
    )

    state = {
        column: 0.0
        for column in result["input_columns"]
    }
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "tools" / "infer_bc_policy.js"
    completed = subprocess.run(
        [
            "node",
            str(script),
            str(output / "policy.json"),
            str(state_path),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    prediction = json.loads(completed.stdout)

    assert prediction["dir_x"] in (-1, 0, 1)
    assert prediction["dir_y"] in (-1, 0, 1)
    assert isinstance(prediction["kick"], bool)
    assert 0.0 <= prediction["kick_probability"] <= 1.0
