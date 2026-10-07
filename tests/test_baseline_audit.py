from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from haxlab.learning.baseline_audit import (
    AUDIT_SCHEMA,
    BaselineAuditError,
    audit_baseline_bundle,
)


def _metric_block(*, threshold: float = 0.5) -> dict:
    samples = 10
    tp, fp, fn, tn = 2, 1, 1, 6
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    f1 = 2 * precision * recall / (precision + recall)
    return {
        "samples": samples,
        "direction_accuracy": 0.7,
        "joint_accuracy": 0.6,
        "kick_precision": precision,
        "kick_recall": recall,
        "kick_f1": f1,
        "kick_true_rate": (tp + fn) / samples,
        "kick_predicted_rate": (tp + fp) / samples,
        "kick_threshold": threshold,
        "kick_confusion": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
        },
        "baselines": {
            "majority_direction_accuracy": 0.4,
            "always_no_kick_accuracy": (tn + fp) / samples,
        },
    }


def _metrics() -> dict:
    return {
        "schema": "haxlab-bc-baseline-v1",
        "created_at": "2026-10-07T06:50:00+00:00",
        "model_path": "/tmp/model/model.npz",
        "train_index": "/tmp/train/_index.json",
        "holdout_index": "/tmp/holdout/_index.json",
        "input_columns": ["own_x", "ball_dx", "ball_dy"],
        "excluded_input_columns": [
            "frame",
            "player_index",
            "team_id",
            "dir_x",
            "dir_y",
            "kick",
        ],
        "direction_classes": [
            {"class_id": index, "dir_x": dx, "dir_y": dy}
            for index, (dx, dy) in enumerate(
                (
                    (-1, -1),
                    (0, -1),
                    (1, -1),
                    (-1, 0),
                    (0, 0),
                    (1, 0),
                    (-1, 1),
                    (0, 1),
                    (1, 1),
                )
            )
        ],
        "architecture": {
            "type": "numpy_mlp_multitask",
            "input_dim": 3,
            "hidden_dim": 2,
            "direction_classes": 9,
            "kick_head": "binary_sigmoid",
        },
        "training": {
            "seed": 1337,
            "calibrated_kick_threshold": 0.35,
            "epochs": 2,
            "batch_size": 64,
            "learning_rate": 0.001,
            "l2": 0.00001,
            "train_replays": 2,
            "holdout_replays": 1,
            "train_stats": {
                "samples": 20,
                "direction_counts": [3, 3, 2, 2, 2, 2, 2, 2, 2],
                "kick_positive": 4,
                "kick_negative": 16,
                "kick_positive_rate": 0.2,
                "kick_positive_weight": 4.0,
            },
        },
        "history": [
            {
                "epoch": 1,
                "train_loss_mean": 1.5,
                "direction_loss_mean": 1.0,
                "kick_loss_mean": 0.5,
                "holdout": _metric_block(threshold=0.5),
            },
            {
                "epoch": 2,
                "train_loss_mean": 1.2,
                "direction_loss_mean": 0.8,
                "kick_loss_mean": 0.4,
                "holdout": _metric_block(threshold=0.5),
            },
        ],
        "final_holdout": _metric_block(threshold=0.35),
    }


def _arrays() -> dict[str, np.ndarray]:
    return {
        "mean": np.array([0.0, 1.0, -1.0], dtype=np.float32),
        "std": np.array([1.0, 2.0, 3.0], dtype=np.float32),
        "w1": np.arange(6, dtype=np.float32).reshape(3, 2) / 10,
        "b1": np.array([0.0, 0.1], dtype=np.float32),
        "wd": np.arange(18, dtype=np.float32).reshape(2, 9) / 20,
        "bd": np.arange(9, dtype=np.float32) / 100,
        "wk": np.array([[0.2], [-0.3]], dtype=np.float32),
        "bk": np.array([0.05], dtype=np.float32),
    }


def _write_bundle(
    tmp_path: Path,
    *,
    metrics: dict | None = None,
    arrays: dict[str, np.ndarray] | None = None,
) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "metrics.json").write_text(
        json.dumps(metrics or _metrics(), indent=2) + "\n",
        encoding="utf-8",
    )
    np.savez_compressed(
        bundle / "model.npz",
        **(arrays or _arrays()),
    )
    return bundle


def test_audit_accepts_clean_bundle_and_is_deterministic(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(tmp_path)

    first = audit_baseline_bundle(bundle)
    second = audit_baseline_bundle(bundle)

    assert first == second
    assert first["schema"] == AUDIT_SCHEMA
    assert first["ok"] is True
    assert first["input_dim"] == 3
    assert first["hidden_dim"] == 2
    assert first["train_replays"] == 2
    assert first["holdout_replays"] == 1
    assert first["final_holdout_samples"] == 10
    assert len(first["metrics_sha256"]) == 64
    assert len(first["model_sha256"]) == 64
    assert len(first["inventory_sha256"]) == 64
    assert set(first["arrays"]) == {
        "mean",
        "std",
        "w1",
        "b1",
        "wd",
        "bd",
        "wk",
        "bk",
    }


def test_audit_rejects_truncated_model_npz(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    path = bundle / "model.npz"
    payload = path.read_bytes()
    path.write_bytes(payload[:-24])

    with pytest.raises(
        BaselineAuditError,
        match="invalid ZIP container",
    ):
        audit_baseline_bundle(bundle)


@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_audit_rejects_model_array_inventory_drift(
    tmp_path: Path,
    mode: str,
) -> None:
    arrays = _arrays()
    if mode == "missing":
        arrays.pop("bk")
    else:
        arrays["unexpected"] = np.zeros(1, dtype=np.float32)
    bundle = _write_bundle(tmp_path, arrays=arrays)

    with pytest.raises(
        BaselineAuditError,
        match="array inventory mismatch",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_model_shape_drift(tmp_path: Path) -> None:
    arrays = _arrays()
    arrays["w1"] = np.zeros((4, 2), dtype=np.float32)
    bundle = _write_bundle(tmp_path, arrays=arrays)

    with pytest.raises(
        BaselineAuditError,
        match="model.w1 shape mismatch",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_non_float32_model_array(
    tmp_path: Path,
) -> None:
    arrays = _arrays()
    arrays["mean"] = arrays["mean"].astype(np.float64)
    bundle = _write_bundle(tmp_path, arrays=arrays)

    with pytest.raises(
        BaselineAuditError,
        match="model.mean must be float32",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_nonfinite_model_array(tmp_path: Path) -> None:
    arrays = _arrays()
    arrays["wk"][0, 0] = np.nan
    bundle = _write_bundle(tmp_path, arrays=arrays)

    with pytest.raises(
        BaselineAuditError,
        match="model.wk contains non-finite",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_nonpositive_normalization_std(
    tmp_path: Path,
) -> None:
    arrays = _arrays()
    arrays["std"][1] = 0.0
    bundle = _write_bundle(tmp_path, arrays=arrays)

    with pytest.raises(
        BaselineAuditError,
        match="model.std must be strictly positive",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_metrics_architecture_drift(
    tmp_path: Path,
) -> None:
    metrics = _metrics()
    metrics["architecture"]["input_dim"] = 4
    bundle = _write_bundle(tmp_path, metrics=metrics)

    with pytest.raises(
        BaselineAuditError,
        match="architecture.input_dim does not match input_columns",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_confusion_count_drift(tmp_path: Path) -> None:
    metrics = _metrics()
    metrics["final_holdout"]["kick_confusion"]["tn"] = 5
    bundle = _write_bundle(tmp_path, metrics=metrics)

    with pytest.raises(
        BaselineAuditError,
        match="kick_confusion does not sum to samples",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_calibrated_threshold_drift(
    tmp_path: Path,
) -> None:
    metrics = _metrics()
    metrics["training"]["calibrated_kick_threshold"] = 0.4
    bundle = _write_bundle(tmp_path, metrics=metrics)

    with pytest.raises(
        BaselineAuditError,
        match="does not match calibrated threshold",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_model_path_drift(tmp_path: Path) -> None:
    metrics = _metrics()
    metrics["model_path"] = "/tmp/model/other.npz"
    bundle = _write_bundle(tmp_path, metrics=metrics)

    with pytest.raises(
        BaselineAuditError,
        match="model_path must end in model.npz",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    path = bundle / "metrics.json"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '"schema": "haxlab-bc-baseline-v1"',
        '"schema": "haxlab-bc-baseline-v1",\n'
        '  "schema": "haxlab-bc-baseline-v1"',
        1,
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(
        BaselineAuditError,
        match="duplicate JSON object key",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_symlinked_model(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    model = bundle / "model.npz"
    target = tmp_path / "model-target.npz"
    target.write_bytes(model.read_bytes())
    model.unlink()
    try:
        model.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(
        BaselineAuditError,
        match="regular non-symlink file",
    ):
        audit_baseline_bundle(bundle)


def test_audit_rejects_symlinked_bundle_root(tmp_path: Path) -> None:
    bundle = _write_bundle(tmp_path)
    link = tmp_path / "bundle-link"
    try:
        link.symlink_to(bundle, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(
        BaselineAuditError,
        match="regular non-symlink directory",
    ):
        audit_baseline_bundle(link)


def test_audit_rejects_metrics_model_hardlink_alias(
    tmp_path: Path,
) -> None:
    bundle = _write_bundle(tmp_path)
    metrics = bundle / "metrics.json"
    model = bundle / "model.npz"
    metrics.unlink()
    try:
        os.link(model, metrics)
    except OSError:
        pytest.skip("hard links unavailable")

    with pytest.raises(
        BaselineAuditError,
        match="must not alias",
    ):
        audit_baseline_bundle(bundle)
