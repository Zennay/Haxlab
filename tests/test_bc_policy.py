from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from haxlab.learning.policy import NumpyBCPolicy


def _write_model(model_dir: Path) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "metrics.json").write_text(
        json.dumps(
            {
                "schema": "haxlab-bc-baseline-v1",
                "input_columns": ["x"],
            }
        ),
        encoding="utf-8",
    )

    wd = np.zeros((1, 9), dtype=np.float32)
    wd[0, 8] = 5.0
    np.savez_compressed(
        model_dir / "model.npz",
        mean=np.array([0.0], dtype=np.float32),
        std=np.array([1.0], dtype=np.float32),
        w1=np.array([[1.0]], dtype=np.float32),
        b1=np.array([0.0], dtype=np.float32),
        wd=wd,
        bd=np.zeros(9, dtype=np.float32),
        wk=np.array([[5.0]], dtype=np.float32),
        bk=np.array([0.0], dtype=np.float32),
    )


def test_policy_loads_and_predicts_action(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)

    policy = NumpyBCPolicy(model_dir)
    prediction = policy.predict({"x": 2.0})

    assert (prediction.dir_x, prediction.dir_y) == (1, 1)
    assert prediction.direction_class == 8
    assert prediction.direction_confidence > 0.9
    assert prediction.kick is True
    assert prediction.kick_probability > 0.9


def test_policy_rejects_missing_features(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    _write_model(model_dir)
    policy = NumpyBCPolicy(model_dir)

    with pytest.raises(ValueError, match="missing model features"):
        policy.predict({})
