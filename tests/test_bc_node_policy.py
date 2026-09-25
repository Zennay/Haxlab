from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_node_portable_policy_predicts_action(tmp_path: Path) -> None:
    model = {
        "schema": "haxlab-bc-portable-v1",
        "input_columns": ["x"],
        "direction_classes": [
            {
                "class_id": i,
                "dir_x": (i % 3) - 1,
                "dir_y": (i // 3) - 1,
            }
            for i in range(9)
        ],
        "mean": [0.0],
        "std": [1.0],
        "w1": [[1.0]],
        "b1": [0.0],
        "wd": [[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 5.0]],
        "bd": [0.0] * 9,
        "wk": [5.0],
        "bk": [0.0],
    }
    model_path = tmp_path / "model.portable.json"
    features_path = tmp_path / "features.json"
    model_path.write_text(json.dumps(model), encoding="utf-8")
    features_path.write_text(json.dumps({"x": 2.0}), encoding="utf-8")

    script = Path(__file__).resolve().parents[1] / "tools" / "predict_bc.js"
    completed = subprocess.run(
        ["node", str(script), str(model_path), str(features_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    prediction = json.loads(completed.stdout)

    assert prediction["direction_class"] == 8
    assert prediction["dir_x"] == 1
    assert prediction["dir_y"] == 1
    assert prediction["direction_confidence"] > 0.9
    assert prediction["kick"] is True
    assert prediction["kick_probability"] > 0.9
