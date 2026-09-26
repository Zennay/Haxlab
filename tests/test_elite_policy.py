from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path

import numpy as np

from haxlab.learning.elite import train_elite_policy
from haxlab.learning.elite_runtime import ElitePolicy


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


def _synthetic_rows(steps: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(steps):
        for player in range(4):
            row = np.zeros(len(COLUMNS), dtype=np.float32)
            ball_dx = float(rng.normal((player - 1.5) * 8, 40))
            ball_dy = float(rng.normal(0, 30))
            row[COLUMNS.index("frame")] = t * 6
            row[COLUMNS.index("player_index")] = player
            row[COLUMNS.index("team_id")] = 1
            row[COLUMNS.index("role_id")] = player
            row[COLUMNS.index("skill_weight")] = 1.0 + player * 0.05
            row[COLUMNS.index("own_x")] = -120 + player * 80
            row[COLUMNS.index("own_y")] = rng.normal(0, 30)
            row[COLUMNS.index("ball_dx")] = ball_dx
            row[COLUMNS.index("ball_dy")] = ball_dy
            row[COLUMNS.index("ball_dvx")] = rng.normal(0, 2)
            row[COLUMNS.index("ball_dvy")] = rng.normal(0, 2)
            for slot in ("tm1_present", "tm2_present", "tm3_present"):
                row[COLUMNS.index(slot)] = 1
            for slot in ("op1_present", "op2_present", "op3_present", "op4_present"):
                row[COLUMNS.index(slot)] = 1
            row[COLUMNS.index("dir_x")] = 1 if ball_dx > 8 else (-1 if ball_dx < -8 else 0)
            row[COLUMNS.index("dir_y")] = 1 if ball_dy > 8 else (-1 if ball_dy < -8 else 0)
            row[COLUMNS.index("kick")] = float(ball_dx * ball_dx + ball_dy * ball_dy < 15 * 15)
            rows.append(row)
    return np.stack(rows)


def _write_shard(root: Path, name: str, rows: np.ndarray) -> dict:
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
    return {
        "replay_sha256": name,
        "shard_path": str(shard),
        "samples": int(rows.shape[0]),
    }


def _write_index(path: Path, entries: list[dict], split: str) -> None:
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


def test_temporal_elite_policy_trains_with_validation_only_calibration(
    tmp_path: Path,
) -> None:
    train_entries = [
        _write_shard(tmp_path / "train", "train-a", _synthetic_rows(180, 1)),
        _write_shard(tmp_path / "train", "train-b", _synthetic_rows(180, 2)),
    ]
    validation_entries = [
        _write_shard(tmp_path / "validation", "validation-a", _synthetic_rows(120, 3))
    ]
    holdout_entries = [
        _write_shard(tmp_path / "holdout", "holdout-a", _synthetic_rows(120, 4))
    ]

    train_index = tmp_path / "train.json"
    validation_index = tmp_path / "validation.json"
    holdout_index = tmp_path / "holdout.json"
    _write_index(train_index, train_entries, "train")
    _write_index(validation_index, validation_entries, "validation")
    _write_index(holdout_index, holdout_entries, "holdout")

    output = tmp_path / "model"
    result = train_elite_policy(
        train_index_path=train_index,
        validation_index_path=validation_index,
        holdout_index_path=holdout_index,
        output_dir=output,
        window=4,
        hidden_dim=32,
        hidden_dim_2=24,
        epochs=4,
        batch_size=128,
        learning_rate=0.004,
        seed=7,
    )

    assert result["schema"] == "haxlab-elite-temporal-policy-v1"
    assert result["architecture"]["window"] == 4
    assert result["training"]["kick_threshold_source"] == "validation_only"
    assert result["training"]["frozen_holdout_used_for_selection"] is False
    calibration = result["training"]["kick_calibration"]
    assert calibration["constraint_satisfied"] is True
    assert calibration["predicted_rate"] <= calibration["predicted_rate_cap"] + 1e-9
    assert result["training"]["best_epoch"] >= 1
    assert result["final_validation"]["samples"] > 0
    assert result["final_holdout"]["samples"] > 0
    assert 0.0 <= result["final_holdout"]["future_direction_accuracy"] <= 1.0
    assert result["final_holdout"]["future_horizon_steps"] == 5
    assert set(result["final_holdout"]["by_role"]) == {"gk", "dm", "am", "st"}
    assert (output / "model.npz").exists()
    assert (output / "runtime-model.json").exists()
    assert (output / "metrics.json").exists()

    runtime_model = json.loads((output / "runtime-model.json").read_text())
    assert runtime_model["schema"] == "haxlab-elite-js-runtime-v1"
    assert runtime_model["window"] == 4
    assert runtime_model["future_horizon_steps"] == 5
    assert runtime_model["base_input_columns"] == result["base_input_columns"]
    assert "wf" in runtime_model["weights"]
    assert "bf" in runtime_model["weights"]
    assert len(runtime_model["weights"]["w1"]) == result["architecture"]["input_dim"]

    policy = ElitePolicy(output)
    features = {name: 0.0 for name in policy.input_columns}
    action = policy.act(agent_id="dm-1", role="dm", features=features)

    assert action["dir_x"] in (-1, 0, 1)
    assert action["dir_y"] in (-1, 0, 1)
    assert isinstance(action["kick"], bool)
    assert 0.0 <= action["kick_probability"] <= 1.0

    node_script = Path(__file__).parents[1] / "tools" / "elite_policy_runtime.js"
    requests = []
    expected = []
    policy.reset("parity-dm")
    for step in range(3):
        state = {
            name: (step + 1) * (index + 1) * 0.01
            for index, name in enumerate(policy.input_columns)
        }
        request = {
            "command": "act",
            "request_id": step + 1,
            "agent_id": "parity-dm",
            "role": "dm",
            "features": state,
        }
        requests.append(json.dumps(request))
        expected.append(
            policy.act(
                agent_id="parity-dm",
                role="dm",
                features=state,
            )
        )

    completed = subprocess.run(
        [
            "node",
            str(node_script),
            str(output / "runtime-model.json"),
        ],
        input="\n".join(requests) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    actual = [
        json.loads(line)
        for line in completed.stdout.splitlines()
        if line.strip()
    ]
    assert len(actual) == len(expected)

    for node_action, python_action in zip(actual, expected):
        assert node_action["ok"] is True
        assert node_action["dir_x"] == python_action["dir_x"]
        assert node_action["dir_y"] == python_action["dir_y"]
        assert node_action["kick"] == python_action["kick"]
        assert node_action["direction_class"] == python_action["direction_class"]
        assert node_action["future_head_available"] is True
        assert python_action["future_head_available"] is True
        assert (
            node_action["future_direction_class"]
            == python_action["future_direction_class"]
        )
        assert node_action["future_dir_x"] == python_action["future_dir_x"]
        assert node_action["future_dir_y"] == python_action["future_dir_y"]
        assert abs(
            node_action["future_direction_probability"]
            - python_action["future_direction_probability"]
        ) < 1e-4
        assert abs(
            node_action["kick_probability"]
            - python_action["kick_probability"]
        ) < 1e-4
        assert abs(
            node_action["direction_probability"]
            - python_action["direction_probability"]
        ) < 1e-4
        assert np.isclose(
            node_action["ood_mean_abs_z"],
            python_action["ood_mean_abs_z"],
            rtol=1e-6,
            atol=1e-3,
        )
        assert np.isclose(
            node_action["ood_max_abs_z"],
            python_action["ood_max_abs_z"],
            rtol=1e-6,
            atol=1e-3,
        )


    far_features = {name: 0.0 for name in policy.input_columns}
    far_features["ball_dx"] = 100.0
    far_features["ball_dy"] = 0.0
    far_python = policy.act(
        agent_id="far-ball",
        role="st",
        features=far_features,
    )
    assert far_python["kick"] is False
    assert far_python["kick_in_range"] is False
    assert far_python["kick_max_distance"] == 31.0

    far_request = json.dumps({
        "command": "act",
        "request_id": 999,
        "agent_id": "far-ball",
        "role": "st",
        "features": far_features,
    }) + "\n"
    far_node = subprocess.run(
        ["node", str(node_script), str(output / "runtime-model.json")],
        input=far_request,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    far_node_action = json.loads(far_node.stdout.strip())
    assert far_node_action["ok"] is True
    assert far_node_action["kick"] is False
    assert far_node_action["kick_in_range"] is False
    assert abs(far_node_action["kick_max_distance"] - 31.0) < 1e-9
