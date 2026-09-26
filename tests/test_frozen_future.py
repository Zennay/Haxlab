from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np

from haxlab.learning.elite import train_elite_policy
from haxlab.learning.frozen_future import (
    BASE_PARAM_KEYS,
    FROZEN_FUTURE_SCHEMA,
    train_frozen_future_head,
)


COLUMNS = [
    "frame", "player_index", "team_id", "role_id", "skill_weight",
    "own_x", "own_y", "own_vx", "own_vy",
    "ball_dx", "ball_dy", "ball_dvx", "ball_dvy",
    "tm1_dx", "tm1_dy", "tm1_dvx", "tm1_dvy", "tm1_present",
    "tm2_dx", "tm2_dy", "tm2_dvx", "tm2_dvy", "tm2_present",
    "tm3_dx", "tm3_dy", "tm3_dvx", "tm3_dvy", "tm3_present",
    "op1_dx", "op1_dy", "op1_dvx", "op1_dvy", "op1_present",
    "op2_dx", "op2_dy", "op2_dvx", "op2_dvy", "op2_present",
    "op3_dx", "op3_dy", "op3_dvx", "op3_dvy", "op3_present",
    "op4_dx", "op4_dy", "op4_dvx", "op4_dvy", "op4_present",
    "score_diff", "dir_x", "dir_y", "kick",
]


def _rows(steps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(steps):
        for player in range(4):
            row = np.zeros(len(COLUMNS), dtype=np.float32)
            phase = (t // 8 + player) % 4
            dx = (-1, 1, 1, -1)[phase]
            dy = (-1, -1, 1, 1)[phase]
            row[COLUMNS.index("frame")] = t * 6
            row[COLUMNS.index("player_index")] = player
            row[COLUMNS.index("team_id")] = 1
            row[COLUMNS.index("role_id")] = player
            row[COLUMNS.index("skill_weight")] = 1.0
            row[COLUMNS.index("own_x")] = -120 + player * 70 + t * dx * 0.7
            row[COLUMNS.index("own_y")] = player * 15 + t * dy * 0.4
            row[COLUMNS.index("own_vx")] = dx
            row[COLUMNS.index("own_vy")] = dy
            row[COLUMNS.index("ball_dx")] = 50 - player * 8 + rng.normal(0, 2)
            row[COLUMNS.index("ball_dy")] = rng.normal(0, 3)
            for name in (
                "tm1_present", "tm2_present", "tm3_present",
                "op1_present", "op2_present", "op3_present", "op4_present",
            ):
                row[COLUMNS.index(name)] = 1
            row[COLUMNS.index("dir_x")] = dx
            row[COLUMNS.index("dir_y")] = dy
            row[COLUMNS.index("kick")] = float((t + player) % 17 == 0)
            rows.append(row)
    return np.stack(rows)


def _shard(root: Path, name: str, rows: np.ndarray) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    shard = root / f"{name}.f32.gz"
    meta = root / f"{name}.meta.json"
    with gzip.open(shard, "wb") as handle:
        handle.write(rows.astype("<f4").tobytes())
    meta.write_text(
        json.dumps(
            {
                "schema": "haxlab-elite-imitation-extract-summary-v1",
                "rowWidth": len(COLUMNS),
                "columns": COLUMNS,
                "sampleEveryTicks": 6,
                "samples": int(rows.shape[0]),
            }
        ),
        encoding="utf-8",
    )
    return {"replay_sha256": name, "shard_path": str(shard), "samples": len(rows)}


def _index(path: Path, split: str, entries: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-elite-shard-index-v1",
                "split": split,
                "sample_every_ticks": 6,
                "entries": entries,
            }
        ),
        encoding="utf-8",
    )


def test_frozen_future_head_preserves_champion_base_exactly(tmp_path: Path) -> None:
    train = tmp_path / "train.json"
    validation = tmp_path / "validation.json"
    holdout = tmp_path / "holdout.json"
    _index(train, "train", [_shard(tmp_path / "train", "a", _rows(70, 1))])
    _index(
        validation,
        "validation",
        [_shard(tmp_path / "validation", "b", _rows(55, 2))],
    )
    _index(
        holdout,
        "holdout",
        [_shard(tmp_path / "holdout", "c", _rows(55, 3))],
    )

    champion_dir = tmp_path / "champion"
    train_elite_policy(
        train_index_path=train,
        validation_index_path=validation,
        holdout_index_path=holdout,
        output_dir=champion_dir,
        window=3,
        hidden_dim=20,
        hidden_dim_2=16,
        epochs=1,
        batch_size=64,
        learning_rate=0.003,
        future_horizon_steps=2,
        seed=41,
    )

    challenger_dir = tmp_path / "frozen-future"
    result = train_frozen_future_head(
        champion_model_dir=champion_dir,
        train_index_path=train,
        validation_index_path=validation,
        holdout_index_path=holdout,
        output_dir=challenger_dir,
        epochs=2,
        batch_size=64,
        learning_rate=0.004,
        future_horizon_steps=2,
        seed=99,
    )

    assert result["schema"] == FROZEN_FUTURE_SCHEMA
    assert result["training"]["mode"] == "future_head_only"
    assert result["training"]["best_epoch"] >= 1
    assert all(result["training"]["frozen_base_parameter_integrity"].values())
    assert result["final_holdout"]["samples"] > 0
    assert (challenger_dir / "runtime-model.json").is_file()

    champion = np.load(champion_dir / "model.npz")
    challenger = np.load(challenger_dir / "model.npz")
    for key in BASE_PARAM_KEYS:
        np.testing.assert_array_equal(champion[key], challenger[key])

    assert "wf" in challenger.files
    assert "bf" in challenger.files

    runtime = json.loads((challenger_dir / "runtime-model.json").read_text())
    assert runtime["source_model_schema"] == FROZEN_FUTURE_SCHEMA
    assert runtime["future_horizon_steps"] == 2
