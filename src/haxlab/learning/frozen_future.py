from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from haxlab.learning.elite import (
    ACTION_DIRS,
    MODEL_SCHEMA,
    ROLE_NAMES,
    _atomic_json,
    _iter_batches,
    _load_index,
    _softmax,
    evaluate,
)

FROZEN_FUTURE_SCHEMA = "haxlab-elite-frozen-future-head-v1"
BASE_PARAM_KEYS = ("w1", "b1", "w2", "b2", "wd", "bd", "wk", "bk")


def _load_champion(
    model_dir: Path,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    metadata = json.loads(
        (model_dir / "metrics.json").read_text(encoding="utf-8")
    )
    arrays = np.load(model_dir / "model.npz")
    params = {
        key: arrays[key].astype(np.float32)
        for key in BASE_PARAM_KEYS
    }
    params["mean"] = arrays["mean"].astype(np.float32)
    params["std"] = arrays["std"].astype(np.float32)
    params["role_weights"] = arrays["role_weights"].astype(np.float32)
    return metadata, params


def _future_metrics(
    index: dict[str, Any],
    *,
    params: dict[str, np.ndarray],
    mean: np.ndarray,
    std: np.ndarray,
    role_weights: np.ndarray,
    window: int,
    sequence_stride: int,
    batch_size: int,
    future_horizon_steps: int,
) -> dict[str, float | int]:
    samples = 0
    correct = 0
    counts = np.zeros(9, dtype=np.int64)
    rng = np.random.default_rng(0)

    for x, _, _, future_direction, _, _ in _iter_batches(
        index,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=batch_size,
        rng=rng,
        shuffle=False,
        future_horizon_steps=future_horizon_steps,
    ):
        h1 = np.maximum(x @ params["w1"] + params["b1"], 0.0)
        h2 = np.maximum(h1 @ params["w2"] + params["b2"], 0.0)
        prob = _softmax(h2 @ params["wf"] + params["bf"])
        pred = prob.argmax(axis=1)
        samples += len(future_direction)
        correct += int((pred == future_direction).sum())
        counts += np.bincount(future_direction, minlength=9)

    majority = int(counts.max()) / max(1, samples)
    accuracy = correct / max(1, samples)
    return {
        "samples": samples,
        "future_direction_accuracy": accuracy,
        "future_majority_direction_accuracy": majority,
        "future_direction_lift": accuracy - majority,
    }


def train_frozen_future_head(
    *,
    champion_model_dir: Path,
    train_index_path: Path,
    validation_index_path: Path,
    holdout_index_path: Path,
    output_dir: Path,
    epochs: int = 3,
    batch_size: int = 2048,
    learning_rate: float = 0.001,
    l2: float = 1e-5,
    future_horizon_steps: int = 5,
    seed: int = 1337,
) -> dict[str, Any]:
    champion_metadata, champion = _load_champion(champion_model_dir)
    train_index = _load_index(train_index_path)
    validation_index = _load_index(validation_index_path)
    holdout_index = _load_index(holdout_index_path)

    architecture = champion_metadata.get("architecture") or {}
    training = champion_metadata.get("training") or {}
    window = int(architecture.get("window") or 8)
    sequence_stride = int(architecture.get("sequence_stride") or 1)
    hidden_dim_2 = int(champion["w2"].shape[1])
    kick_threshold = float(training.get("calibrated_kick_threshold", 0.5))

    mean = champion["mean"]
    std = champion["std"]
    role_weights = champion["role_weights"]
    params = {key: champion[key].copy() for key in BASE_PARAM_KEYS}

    rng = np.random.default_rng(seed)
    scale = math.sqrt(2.0 / max(1, hidden_dim_2))
    params["wf"] = rng.normal(
        0.0,
        scale,
        (hidden_dim_2, 9),
    ).astype(np.float32)
    params["bf"] = np.zeros(9, dtype=np.float32)

    m = {
        "wf": np.zeros_like(params["wf"]),
        "bf": np.zeros_like(params["bf"]),
    }
    v = {
        "wf": np.zeros_like(params["wf"]),
        "bf": np.zeros_like(params["bf"]),
    }
    step = 0
    best_accuracy = -1.0
    best_epoch = 0
    best_wf = params["wf"].copy()
    best_bf = params["bf"].copy()
    history: list[dict[str, Any]] = []

    for epoch in range(1, max(1, epochs) + 1):
        losses: list[float] = []
        samples = 0
        for x, _, _, future_direction, _, sample_weight in _iter_batches(
            train_index,
            mean=mean,
            std=std,
            role_weights=role_weights,
            window=window,
            sequence_stride=sequence_stride,
            batch_size=max(32, batch_size),
            rng=rng,
            shuffle=True,
            future_horizon_steps=future_horizon_steps,
        ):
            h1 = np.maximum(x @ params["w1"] + params["b1"], 0.0)
            h2 = np.maximum(h1 @ params["w2"] + params["b2"], 0.0)
            prob = _softmax(h2 @ params["wf"] + params["bf"])

            sw = sample_weight.astype(np.float32, copy=False)
            sw_sum = max(1e-6, float(sw.sum()))
            eps = 1e-7
            loss = -float(
                (
                    sw
                    * np.log(
                        np.clip(
                            prob[np.arange(len(future_direction)), future_direction],
                            eps,
                            1.0,
                        )
                    )
                ).sum()
                / sw_sum
            )

            dlogits = prob.copy()
            dlogits[np.arange(len(future_direction)), future_direction] -= 1.0
            dlogits *= (sw / sw_sum)[:, None]
            grads = {
                "wf": (
                    h2.T @ dlogits + max(0.0, l2) * params["wf"]
                ).astype(np.float32),
                "bf": dlogits.sum(axis=0).astype(np.float32),
            }

            step += 1
            for key in ("wf", "bf"):
                grad = grads[key]
                m[key] = 0.9 * m[key] + 0.1 * grad
                v[key] = 0.999 * v[key] + 0.001 * np.square(grad)
                m_hat = m[key] / (1.0 - 0.9**step)
                v_hat = v[key] / (1.0 - 0.999**step)
                params[key] -= (
                    learning_rate
                    * m_hat
                    / (np.sqrt(v_hat) + 1e-8)
                )

            losses.append(loss)
            samples += len(future_direction)

        validation_future = _future_metrics(
            validation_index,
            params=params,
            mean=mean,
            std=std,
            role_weights=role_weights,
            window=window,
            sequence_stride=sequence_stride,
            batch_size=max(32, batch_size),
            future_horizon_steps=future_horizon_steps,
        )
        accuracy = float(validation_future["future_direction_accuracy"])
        if accuracy > best_accuracy:
            best_accuracy = accuracy
            best_epoch = epoch
            best_wf = params["wf"].copy()
            best_bf = params["bf"].copy()

        row = {
            "epoch": epoch,
            "samples": samples,
            "train_future_loss_mean": float(np.mean(losses)),
            "validation": validation_future,
        }
        history.append(row)
        print(
            json.dumps(
                {"event": "frozen_future_epoch_complete", **row},
                sort_keys=True,
            ),
            flush=True,
        )

    params["wf"] = best_wf
    params["bf"] = best_bf

    validation_final, _, _ = evaluate(
        validation_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=max(32, batch_size),
        kick_threshold=kick_threshold,
        future_horizon_steps=future_horizon_steps,
    )
    holdout_final, _, _ = evaluate(
        holdout_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=max(32, batch_size),
        kick_threshold=kick_threshold,
        future_horizon_steps=future_horizon_steps,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez_compressed(
        model_path,
        mean=mean,
        std=std,
        role_weights=role_weights,
        **params,
    )

    runtime_model = {
        "schema": "haxlab-elite-js-runtime-v1",
        "source_model_schema": FROZEN_FUTURE_SCHEMA,
        "window": window,
        "base_input_columns": list(
            champion_metadata.get("base_input_columns") or []
        ),
        "role_ids": {name: role_id for role_id, name in ROLE_NAMES.items()},
        "direction_classes": [
            {"class_id": i, "dir_x": dx, "dir_y": dy}
            for i, (dx, dy) in enumerate(ACTION_DIRS)
        ],
        "kick_threshold": kick_threshold,
        "kick_max_distance": float(
            (champion_metadata.get("runtime") or {}).get(
                "kick_max_distance",
                31.0,
            )
        ),
        "future_horizon_steps": future_horizon_steps,
        "future_deadzone": 8.0,
        "mean": mean.astype(float).tolist(),
        "std": std.astype(float).tolist(),
        "weights": {
            key: value.astype(float).tolist()
            for key, value in params.items()
        },
    }
    runtime_path = output_dir / "runtime-model.json"
    _atomic_json(runtime_path, runtime_model)

    frozen_integrity = {}
    champion_arrays = np.load(champion_model_dir / "model.npz")
    for key in BASE_PARAM_KEYS:
        frozen_integrity[key] = bool(
            np.array_equal(params[key], champion_arrays[key])
        )
    if not all(frozen_integrity.values()):
        raise AssertionError("frozen champion parameters changed during training")

    metadata = {
        "schema": FROZEN_FUTURE_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "champion_model_dir": str(champion_model_dir),
        "source_champion_schema": champion_metadata.get("schema", MODEL_SCHEMA),
        "model_path": str(model_path),
        "runtime_model_path": str(runtime_path),
        "base_input_columns": list(
            champion_metadata.get("base_input_columns") or []
        ),
        "architecture": {
            **architecture,
            "future_direction_head": "frozen_backbone_9_way",
            "future_horizon_steps": future_horizon_steps,
        },
        "runtime": {
            **(champion_metadata.get("runtime") or {}),
            "future_head_mode": "frozen_champion_backbone",
        },
        "training": {
            "mode": "future_head_only",
            "seed": seed,
            "epochs_requested": max(1, epochs),
            "best_epoch": best_epoch,
            "batch_size": max(32, batch_size),
            "learning_rate": learning_rate,
            "l2": max(0.0, l2),
            "future_horizon_steps": future_horizon_steps,
            "calibrated_kick_threshold": kick_threshold,
            "kick_threshold_source": "frozen_champion",
            "frozen_base_parameter_integrity": frozen_integrity,
        },
        "history": history,
        "final_validation": validation_final,
        "final_holdout": holdout_final,
    }
    _atomic_json(output_dir / "metrics.json", metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-train-frozen-future")
    parser.add_argument("--champion-model-dir", type=Path, required=True)
    parser.add_argument("--train-index", type=Path, required=True)
    parser.add_argument("--validation-index", type=Path, required=True)
    parser.add_argument("--holdout-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--l2", type=float, default=1e-5)
    parser.add_argument("--future-horizon-steps", type=int, default=5)
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    result = train_frozen_future_head(
        champion_model_dir=args.champion_model_dir,
        train_index_path=args.train_index,
        validation_index_path=args.validation_index,
        holdout_index_path=args.holdout_index,
        output_dir=args.output_dir,
        epochs=max(1, args.epochs),
        batch_size=max(32, args.batch_size),
        learning_rate=max(1e-6, args.learning_rate),
        l2=max(0.0, args.l2),
        future_horizon_steps=max(1, args.future_horizon_steps),
        seed=args.seed,
    )
    print(json.dumps(result["final_holdout"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
