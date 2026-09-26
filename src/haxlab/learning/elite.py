from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import numpy as np


MODEL_SCHEMA = "haxlab-elite-temporal-policy-v1"
RUNTIME_KICK_MAX_DISTANCE = 31.0
ACTION_DIRS = tuple(
    (dx, dy)
    for dy in (-1, 0, 1)
    for dx in (-1, 0, 1)
)
DIR_TO_CLASS = {direction: index for index, direction in enumerate(ACTION_DIRS)}
ROLE_NAMES = {0: "gk", 1: "dm", 2: "am", 3: "st"}
LABEL_COLUMNS = ("dir_x", "dir_y", "kick")
META_COLUMNS = ("frame", "player_index", "team_id", "role_id", "skill_weight")
EXCLUDED_INPUT_COLUMNS = (*META_COLUMNS, *LABEL_COLUMNS)


def direction_from_class(class_id: int) -> tuple[int, int]:
    return ACTION_DIRS[int(class_id)]


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _load_index(path: Path, limit: int | None = None) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = list(payload.get("entries") or [])
    if limit is not None:
        entries = entries[: max(0, limit)]
    return {**payload, "entries": entries}


def _load_shard(entry: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    shard_path = Path(str(entry["shard_path"]))
    meta_path = shard_path.parent / (
        shard_path.name.removesuffix(".f32.gz") + ".meta.json"
    )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    row_width = int(meta["rowWidth"])
    columns = list(meta["columns"])

    with gzip.open(shard_path, "rb") as handle:
        raw = handle.read()
    values = np.frombuffer(raw, dtype="<f4")
    if row_width <= 0 or values.size % row_width != 0:
        raise ValueError(
            f"{shard_path}: invalid float32 size {values.size} for row width {row_width}"
        )
    return values.reshape(-1, row_width), columns


def _layout(columns: list[str]) -> dict[str, Any]:
    by_name = {name: index for index, name in enumerate(columns)}
    missing = [
        name
        for name in (*META_COLUMNS, *LABEL_COLUMNS)
        if name not in by_name
    ]
    if missing:
        raise ValueError(f"elite shard missing columns: {missing}")
    input_indices = [
        index for index, name in enumerate(columns)
        if name not in EXCLUDED_INPUT_COLUMNS
    ]
    return {
        "by_name": by_name,
        "input_indices": input_indices,
        "input_columns": [columns[index] for index in input_indices],
    }


def _extract_frame_arrays(
    rows: np.ndarray,
    columns: list[str],
) -> dict[str, Any]:
    layout = _layout(columns)
    by_name = layout["by_name"]
    x = rows[:, layout["input_indices"]].astype(np.float32, copy=False)
    dx = np.clip(np.rint(rows[:, by_name["dir_x"]]), -1, 1).astype(np.int8)
    dy = np.clip(np.rint(rows[:, by_name["dir_y"]]), -1, 1).astype(np.int8)
    direction = ((dy + 1) * 3 + (dx + 1)).astype(np.int64)
    kick = (rows[:, by_name["kick"]] > 0.5).astype(np.float32)
    return {
        "x": x,
        "frame": rows[:, by_name["frame"]].astype(np.int64),
        "player": rows[:, by_name["player_index"]].astype(np.int64),
        "role": rows[:, by_name["role_id"]].astype(np.int64),
        "skill_weight": np.clip(
            rows[:, by_name["skill_weight"]].astype(np.float32),
            0.1,
            5.0,
        ),
        "direction": direction,
        "kick": kick,
        "input_columns": layout["input_columns"],
    }


def _normalization(
    index: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    count = 0
    total: np.ndarray | None = None
    total_sq: np.ndarray | None = None
    input_columns: list[str] | None = None
    direction_counts = np.zeros(9, dtype=np.int64)
    role_counts = np.zeros(4, dtype=np.int64)
    kick_positive = 0

    for entry in index["entries"]:
        rows, columns = _load_shard(entry)
        arrays = _extract_frame_arrays(rows, columns)
        x = arrays["x"]
        shard_columns = arrays["input_columns"]
        if input_columns is None:
            input_columns = shard_columns
            total = np.zeros(x.shape[1], dtype=np.float64)
            total_sq = np.zeros(x.shape[1], dtype=np.float64)
        elif shard_columns != input_columns:
            raise ValueError("inconsistent elite shard input columns")

        assert total is not None and total_sq is not None
        total += x.sum(axis=0, dtype=np.float64)
        total_sq += np.square(x, dtype=np.float64).sum(axis=0, dtype=np.float64)
        count += x.shape[0]
        direction_counts += np.bincount(arrays["direction"], minlength=9)
        kick_positive += int(arrays["kick"].sum())
        valid_roles = arrays["role"][
            (arrays["role"] >= 0) & (arrays["role"] <= 3)
        ]
        role_counts += np.bincount(valid_roles, minlength=4)[:4]

    if count <= 0 or total is None or total_sq is None or input_columns is None:
        raise ValueError("elite training index contains no frame samples")

    mean = total / count
    variance = np.maximum(total_sq / count - np.square(mean), 1e-8)
    std = np.sqrt(variance)
    std = np.where(std < 1e-4, 1.0, std)

    kick_negative = count - kick_positive
    # Weighted BCE still needs restraint. Too much positive weight makes a
    # policy spam kick; sqrt compression is intentionally conservative.
    kick_pos_weight = (
        min(12.0, max(1.0, math.sqrt(kick_negative / max(1, kick_positive))))
        if kick_positive
        else 1.0
    )

    nonzero_roles = max(1, int((role_counts > 0).sum()))
    role_weights = np.ones(4, dtype=np.float32)
    for role_id in range(4):
        if role_counts[role_id] > 0:
            raw = math.sqrt(count / (nonzero_roles * int(role_counts[role_id])))
            role_weights[role_id] = max(0.60, min(2.50, raw))

    stats = {
        "frame_samples": count,
        "direction_counts": direction_counts.tolist(),
        "kick_positive": kick_positive,
        "kick_negative": kick_negative,
        "kick_positive_rate": kick_positive / count,
        "kick_positive_weight": kick_pos_weight,
        "role_counts": {
            ROLE_NAMES[i]: int(role_counts[i]) for i in range(4)
        },
        "role_weights": {
            ROLE_NAMES[i]: float(role_weights[i]) for i in range(4)
        },
    }
    return (
        mean.astype(np.float32),
        std.astype(np.float32),
        input_columns,
        {**stats, "_role_weights_array": role_weights},
    )


def _segment_indices(frames: np.ndarray, max_gap: int) -> list[tuple[int, int]]:
    if len(frames) == 0:
        return []
    breaks = np.where(np.diff(frames) > max_gap)[0] + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [len(frames)]))
    return [(int(start), int(end)) for start, end in zip(starts, ends)]


def _future_direction_classes(
    current_x: np.ndarray,
    current_y: np.ndarray,
    future_x: np.ndarray,
    future_y: np.ndarray,
    *,
    deadzone: float = 8.0,
) -> np.ndarray:
    dx = future_x - current_x
    dy = future_y - current_y
    qx = np.where(dx > deadzone, 1, np.where(dx < -deadzone, -1, 0))
    qy = np.where(dy > deadzone, 1, np.where(dy < -deadzone, -1, 0))
    return ((qy + 1) * 3 + (qx + 1)).astype(np.int64)


def _iter_batches(
    index: dict[str, Any],
    *,
    mean: np.ndarray,
    std: np.ndarray,
    role_weights: np.ndarray,
    window: int,
    sequence_stride: int,
    batch_size: int,
    rng: np.random.Generator,
    shuffle: bool,
    future_horizon_steps: int = 5,
) -> Iterator[
    tuple[
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
        np.ndarray,
    ]
]:
    entries = list(index["entries"])
    if shuffle:
        rng.shuffle(entries)

    sample_every = max(1, int(index.get("sample_every_ticks") or 6))
    max_gap = sample_every * 3
    role_eye = np.eye(4, dtype=np.float32)
    future_horizon_steps = max(1, int(future_horizon_steps))

    for entry in entries:
        rows, columns = _load_shard(entry)
        arrays = _extract_frame_arrays(rows, columns)
        raw_x = arrays["x"]
        x = ((raw_x - mean) / std).astype(np.float32, copy=False)
        own_x_index = arrays["input_columns"].index("own_x")
        own_y_index = arrays["input_columns"].index("own_y")

        unique_players = np.unique(arrays["player"])
        if shuffle:
            rng.shuffle(unique_players)

        for player_id in unique_players:
            idx = np.flatnonzero(arrays["player"] == player_id)
            if len(idx) < window + future_horizon_steps:
                continue
            order = np.argsort(arrays["frame"][idx], kind="stable")
            idx = idx[order]

            frames = arrays["frame"][idx]
            px = x[idx]
            praw = raw_x[idx]
            pdirection = arrays["direction"][idx]
            pkick = arrays["kick"][idx]
            prole = arrays["role"][idx]
            pskill = arrays["skill_weight"][idx]

            for start, end in _segment_indices(frames, max_gap):
                length = end - start
                if length < window + future_horizon_steps:
                    continue
                endpoints = np.arange(
                    start + window - 1,
                    end - future_horizon_steps,
                    max(1, sequence_stride),
                    dtype=np.int64,
                )
                if shuffle:
                    rng.shuffle(endpoints)

                for batch_start in range(0, len(endpoints), batch_size):
                    ep = endpoints[batch_start : batch_start + batch_size]
                    sequence = np.stack(
                        [
                            px[ep - (window - 1 - offset)]
                            for offset in range(window)
                        ],
                        axis=1,
                    )
                    flat = sequence.reshape(sequence.shape[0], -1)

                    roles = prole[ep]
                    valid = (roles >= 0) & (roles <= 3)
                    one_hot = np.zeros((len(ep), 4), dtype=np.float32)
                    one_hot[valid] = role_eye[roles[valid]]
                    model_x = np.concatenate([flat, one_hot], axis=1)

                    sample_weight = pskill[ep].astype(np.float32, copy=True)
                    for role_id in range(4):
                        mask = roles == role_id
                        if mask.any():
                            sample_weight[mask] *= role_weights[role_id]
                    sample_weight = np.clip(sample_weight, 0.1, 5.0)

                    future_ep = ep + future_horizon_steps
                    future_direction = _future_direction_classes(
                        praw[ep, own_x_index],
                        praw[ep, own_y_index],
                        praw[future_ep, own_x_index],
                        praw[future_ep, own_y_index],
                    )

                    yield (
                        model_x.astype(np.float32, copy=False),
                        pdirection[ep],
                        pkick[ep],
                        future_direction,
                        roles,
                        sample_weight,
                    )


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=1, keepdims=True)
    exp = np.exp(shifted)
    return exp / exp.sum(axis=1, keepdims=True)


def _sigmoid(logits: np.ndarray) -> np.ndarray:
    clipped = np.clip(logits, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _init_params(
    input_dim: int,
    hidden_dim: int,
    hidden_dim_2: int,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    scale1 = math.sqrt(2.0 / max(1, input_dim))
    scale2 = math.sqrt(2.0 / max(1, hidden_dim))
    scale3 = math.sqrt(2.0 / max(1, hidden_dim_2))
    return {
        "w1": rng.normal(0.0, scale1, (input_dim, hidden_dim)).astype(np.float32),
        "b1": np.zeros(hidden_dim, dtype=np.float32),
        "w2": rng.normal(0.0, scale2, (hidden_dim, hidden_dim_2)).astype(np.float32),
        "b2": np.zeros(hidden_dim_2, dtype=np.float32),
        "wd": rng.normal(0.0, scale3, (hidden_dim_2, 9)).astype(np.float32),
        "bd": np.zeros(9, dtype=np.float32),
        "wk": rng.normal(0.0, scale3, (hidden_dim_2, 1)).astype(np.float32),
        "bk": np.zeros(1, dtype=np.float32),
        "wf": rng.normal(0.0, scale3, (hidden_dim_2, 9)).astype(np.float32),
        "bf": np.zeros(9, dtype=np.float32),
    }


def _forward(
    x: np.ndarray,
    params: dict[str, np.ndarray],
) -> tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    pre1 = x @ params["w1"] + params["b1"]
    h1 = np.maximum(pre1, 0.0)
    pre2 = h1 @ params["w2"] + params["b2"]
    h2 = np.maximum(pre2, 0.0)
    dir_prob = _softmax(h2 @ params["wd"] + params["bd"])
    kick_prob = _sigmoid(h2 @ params["wk"] + params["bk"]).reshape(-1)
    future_prob = _softmax(h2 @ params["wf"] + params["bf"])
    return h1, h2, dir_prob, kick_prob, future_prob


def _adam_update(
    params: dict[str, np.ndarray],
    grads: dict[str, np.ndarray],
    m: dict[str, np.ndarray],
    v: dict[str, np.ndarray],
    *,
    step: int,
    learning_rate: float,
    beta1: float = 0.9,
    beta2: float = 0.999,
    epsilon: float = 1e-8,
) -> None:
    for key, param in params.items():
        grad = grads[key]
        m[key] *= beta1
        m[key] += (1.0 - beta1) * grad
        v[key] *= beta2
        v[key] += (1.0 - beta2) * np.square(grad)
        m_hat = m[key] / (1.0 - beta1**step)
        v_hat = v[key] / (1.0 - beta2**step)
        param -= learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)


def _train_batch(
    x: np.ndarray,
    direction: np.ndarray,
    kick: np.ndarray,
    future_direction: np.ndarray,
    sample_weight: np.ndarray,
    params: dict[str, np.ndarray],
    *,
    kick_pos_weight: float,
    future_loss_weight: float,
    l2: float,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    batch = x.shape[0]
    h1, h2, dir_prob, kick_prob, future_prob = _forward(x, params)
    eps = 1e-7

    sw = sample_weight.astype(np.float32, copy=False)
    sw_sum = max(1e-6, float(sw.sum()))
    dir_loss = -float(
        (sw * np.log(np.clip(dir_prob[np.arange(batch), direction], eps, 1.0))).sum()
        / sw_sum
    )
    future_loss = -float(
        (
            sw
            * np.log(
                np.clip(
                    future_prob[np.arange(batch), future_direction],
                    eps,
                    1.0,
                )
            )
        ).sum()
        / sw_sum
    )

    kick_weights = sw * np.where(kick > 0.5, kick_pos_weight, 1.0).astype(np.float32)
    kick_weight_sum = max(1e-6, float(kick_weights.sum()))
    kick_loss = -float(
        (
            kick_weights
            * (
                kick * np.log(np.clip(kick_prob, eps, 1.0))
                + (1.0 - kick) * np.log(np.clip(1.0 - kick_prob, eps, 1.0))
            )
        ).sum()
        / kick_weight_sum
    )

    ddir = dir_prob.copy()
    ddir[np.arange(batch), direction] -= 1.0
    ddir *= (sw / sw_sum)[:, None]

    dfuture = future_prob.copy()
    dfuture[np.arange(batch), future_direction] -= 1.0
    dfuture *= (sw / sw_sum)[:, None]
    dfuture *= float(future_loss_weight)

    dkick = (
        (kick_prob - kick)
        * kick_weights
        / kick_weight_sum
    ).reshape(-1, 1)

    grad_wd = h2.T @ ddir + l2 * params["wd"]
    grad_bd = ddir.sum(axis=0)
    grad_wk = h2.T @ dkick + l2 * params["wk"]
    grad_bk = dkick.sum(axis=0)
    grad_wf = h2.T @ dfuture + l2 * params["wf"]
    grad_bf = dfuture.sum(axis=0)

    dh2 = (
        ddir @ params["wd"].T
        + dkick @ params["wk"].T
        + dfuture @ params["wf"].T
    )
    dpre2 = dh2 * (h2 > 0.0)
    grad_w2 = h1.T @ dpre2 + l2 * params["w2"]
    grad_b2 = dpre2.sum(axis=0)

    dh1 = dpre2 @ params["w2"].T
    dpre1 = dh1 * (h1 > 0.0)
    grad_w1 = x.T @ dpre1 + l2 * params["w1"]
    grad_b1 = dpre1.sum(axis=0)

    grads = {
        "w1": grad_w1.astype(np.float32),
        "b1": grad_b1.astype(np.float32),
        "w2": grad_w2.astype(np.float32),
        "b2": grad_b2.astype(np.float32),
        "wd": grad_wd.astype(np.float32),
        "bd": grad_bd.astype(np.float32),
        "wk": grad_wk.astype(np.float32),
        "bk": grad_bk.astype(np.float32),
        "wf": grad_wf.astype(np.float32),
        "bf": grad_bf.astype(np.float32),
    }
    return grads, {
        "direction_loss": dir_loss,
        "kick_loss": kick_loss,
        "future_direction_loss": future_loss,
        "loss": dir_loss + kick_loss + float(future_loss_weight) * future_loss,
    }


def _empty_metric_counter() -> dict[str, Any]:
    return {
        "samples": 0,
        "direction_correct": 0,
        "joint_correct": 0,
        "kick_tp": 0,
        "kick_fp": 0,
        "kick_fn": 0,
        "kick_tn": 0,
        "direction_counts": np.zeros(9, dtype=np.int64),
        "direction_correct_by_class": np.zeros(9, dtype=np.int64),
    }


def _finalize_metrics(counter: dict[str, Any], threshold: float) -> dict[str, Any]:
    total = int(counter["samples"])
    if total <= 0:
        return {"samples": 0, "kick_threshold": float(threshold)}
    tp = int(counter["kick_tp"])
    fp = int(counter["kick_fp"])
    fn = int(counter["kick_fn"])
    tn = int(counter["kick_tn"])
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
    direction_counts = counter["direction_counts"]
    direction_correct_by_class = counter["direction_correct_by_class"]
    recalls = [
        (
            int(direction_correct_by_class[class_id])
            / int(direction_counts[class_id])
            if int(direction_counts[class_id]) > 0
            else None
        )
        for class_id in range(9)
    ]
    observed_recalls = [value for value in recalls if value is not None]
    macro_recall = (
        float(sum(observed_recalls) / len(observed_recalls))
        if observed_recalls
        else 0.0
    )
    return {
        "samples": total,
        "direction_accuracy": int(counter["direction_correct"]) / total,
        "macro_direction_recall": macro_recall,
        "direction_recall_by_class": {
            str(class_id): {
                "dir_x": ACTION_DIRS[class_id][0],
                "dir_y": ACTION_DIRS[class_id][1],
                "samples": int(direction_counts[class_id]),
                "recall": recalls[class_id],
            }
            for class_id in range(9)
        },
        "joint_accuracy": int(counter["joint_correct"]) / total,
        "kick_precision": precision,
        "kick_recall": recall,
        "kick_f1": f1,
        "kick_true_rate": (tp + fn) / total,
        "kick_predicted_rate": (tp + fp) / total,
        "kick_threshold": float(threshold),
        "kick_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "baselines": {
            "majority_direction_accuracy": int(direction_counts.max()) / total,
            "always_no_kick_accuracy": (tn + fp) / total,
        },
    }


def _update_counter(
    counter: dict[str, Any],
    direction: np.ndarray,
    dir_pred: np.ndarray,
    kick_true: np.ndarray,
    kick_pred: np.ndarray,
) -> None:
    counter["samples"] += len(direction)
    counter["direction_correct"] += int((dir_pred == direction).sum())
    for class_id in range(9):
        mask = direction == class_id
        if mask.any():
            counter["direction_correct_by_class"][class_id] += int(
                (dir_pred[mask] == class_id).sum()
            )
    counter["joint_correct"] += int(
        ((dir_pred == direction) & (kick_pred == kick_true)).sum()
    )
    counter["kick_tp"] += int((kick_pred & kick_true).sum())
    counter["kick_fp"] += int((kick_pred & ~kick_true).sum())
    counter["kick_fn"] += int((~kick_pred & kick_true).sum())
    counter["kick_tn"] += int((~kick_pred & ~kick_true).sum())
    counter["direction_counts"] += np.bincount(direction, minlength=9)


def evaluate(
    index: dict[str, Any],
    *,
    params: dict[str, np.ndarray],
    mean: np.ndarray,
    std: np.ndarray,
    role_weights: np.ndarray,
    window: int,
    sequence_stride: int,
    batch_size: int,
    kick_threshold: float,
    kick_thresholds_by_role: dict[int, float] | None = None,
    future_horizon_steps: int = 5,
    collect_kick: bool = False,
) -> tuple[dict[str, Any], np.ndarray | None, np.ndarray | None]:
    overall = _empty_metric_counter()
    by_role = {role_id: _empty_metric_counter() for role_id in range(4)}
    collected_probs: list[np.ndarray] = []
    collected_true: list[np.ndarray] = []
    future_samples = 0
    future_correct = 0
    future_counts = np.zeros(9, dtype=np.int64)

    rng = np.random.default_rng(0)
    for x, direction, kick, future_direction, roles, _ in _iter_batches(
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
        _, _, dir_prob, kick_prob, future_prob = _forward(x, params)
        dir_pred = dir_prob.argmax(axis=1)
        future_pred = future_prob.argmax(axis=1)
        kick_true = kick > 0.5
        thresholds = np.full(
            len(kick_prob),
            float(kick_threshold),
            dtype=np.float32,
        )
        if kick_thresholds_by_role:
            for role_id, role_threshold in kick_thresholds_by_role.items():
                thresholds[roles == int(role_id)] = float(role_threshold)
        kick_pred = kick_prob >= thresholds
        _update_counter(overall, direction, dir_pred, kick_true, kick_pred)

        future_samples += len(future_direction)
        future_correct += int((future_pred == future_direction).sum())
        future_counts += np.bincount(future_direction, minlength=9)

        for role_id in range(4):
            mask = roles == role_id
            if mask.any():
                _update_counter(
                    by_role[role_id],
                    direction[mask],
                    dir_pred[mask],
                    kick_true[mask],
                    kick_pred[mask],
                )

        if collect_kick:
            collected_probs.append(kick_prob.astype(np.float32, copy=True))
            collected_true.append(kick_true.astype(bool, copy=True))

    metrics = _finalize_metrics(overall, kick_threshold)
    if kick_thresholds_by_role:
        metrics["kick_threshold_mode"] = "per_role"
        metrics["kick_thresholds_by_role"] = {
            ROLE_NAMES[role_id]: float(
                kick_thresholds_by_role.get(role_id, kick_threshold)
            )
            for role_id in range(4)
        }
    metrics["future_direction_accuracy"] = (
        future_correct / future_samples if future_samples else 0.0
    )
    metrics["future_majority_direction_accuracy"] = (
        int(future_counts.max()) / future_samples if future_samples else 0.0
    )
    metrics["future_direction_lift"] = (
        metrics["future_direction_accuracy"]
        - metrics["future_majority_direction_accuracy"]
    )
    metrics["future_horizon_steps"] = int(future_horizon_steps)
    metrics["by_role"] = {
        ROLE_NAMES[role_id]: _finalize_metrics(
            counter,
            (
                kick_thresholds_by_role.get(role_id, kick_threshold)
                if kick_thresholds_by_role
                else kick_threshold
            ),
        )
        for role_id, counter in by_role.items()
        if counter["samples"] > 0
    }

    probs = np.concatenate(collected_probs) if collected_probs else None
    truth = np.concatenate(collected_true) if collected_true else None
    return metrics, probs, truth


def _collect_kick_predictions(
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
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    probs: list[np.ndarray] = []
    truth: list[np.ndarray] = []
    roles_out: list[np.ndarray] = []
    rng = np.random.default_rng(0)

    for x, _, kick, _, roles, _ in _iter_batches(
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
        _, _, _, kick_prob, _ = _forward(x, params)
        probs.append(kick_prob.astype(np.float32, copy=True))
        truth.append((kick > 0.5).astype(bool, copy=True))
        roles_out.append(roles.astype(np.int64, copy=True))

    if not probs:
        raise ValueError("split has no kick predictions")
    return (
        np.concatenate(probs),
        np.concatenate(truth),
        np.concatenate(roles_out),
    )


def _kick_threshold_metrics(
    probs: np.ndarray,
    truth: np.ndarray,
    threshold: float,
) -> dict[str, float]:
    pred = probs >= threshold
    tp = int((pred & truth).sum())
    fp = int((pred & ~truth).sum())
    fn = int((~pred & truth).sum())
    precision = tp / max(1, tp + fp)
    recall = tp / max(1, tp + fn)
    f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
    return {
        "threshold": float(threshold),
        "f1": float(f1),
        "precision": float(precision),
        "recall": float(recall),
        "predicted_rate": float(pred.mean()),
        "true_rate": float(truth.mean()),
    }


def _calibrate_kick_threshold(
    probs: np.ndarray,
    truth: np.ndarray,
    *,
    max_rate_multiplier: float = 1.5,
) -> tuple[float, dict[str, float], list[dict[str, float]]]:
    """Choose a validation-only kick threshold without allowing kick spam.

    Pure F1 calibration can prefer a threshold that predicts several times the
    human kick frequency. That may look acceptable offline while producing an
    obnoxious live agent. Keep candidates whose predicted kick rate is at most
    1.5x the validation human rate (with a 1% floor for very tiny samples), then
    choose the best F1 within that gameplay-safe set.
    """
    thresholds = np.linspace(0.05, 0.95, 37)
    candidates = [
        _kick_threshold_metrics(probs, truth, float(threshold))
        for threshold in thresholds
    ]
    true_rate = float(truth.mean())
    rate_cap = max(0.01, true_rate * max_rate_multiplier)
    feasible = [
        row for row in candidates
        if row["predicted_rate"] <= rate_cap
    ]
    pool = feasible or candidates
    best = max(
        pool,
        key=lambda row: (
            row["f1"],
            -abs(row["predicted_rate"] - true_rate),
            -abs(row["threshold"] - 0.5),
        ),
    )
    best = {
        **best,
        "predicted_rate_cap": float(rate_cap),
        "max_rate_multiplier": float(max_rate_multiplier),
        "constraint_satisfied": bool(best["predicted_rate"] <= rate_cap),
    }
    return float(best["threshold"]), best, candidates


def train_elite_policy(
    *,
    train_index_path: Path,
    validation_index_path: Path,
    holdout_index_path: Path,
    output_dir: Path,
    train_limit: int | None = None,
    validation_limit: int | None = None,
    holdout_limit: int | None = None,
    window: int = 8,
    sequence_stride: int = 1,
    hidden_dim: int = 128,
    hidden_dim_2: int = 96,
    epochs: int = 5,
    batch_size: int = 2048,
    learning_rate: float = 8e-4,
    l2: float = 1e-5,
    future_horizon_steps: int = 5,
    future_loss_weight: float = 0.35,
    seed: int = 1337,
    progress_path: Path | None = None,
) -> dict[str, Any]:
    train_index = _load_index(train_index_path, train_limit)
    validation_index = _load_index(validation_index_path, validation_limit)
    holdout_index = _load_index(holdout_index_path, holdout_limit)

    mean, std, input_columns, train_stats = _normalization(train_index)
    role_weights = np.asarray(
        train_stats.pop("_role_weights_array"),
        dtype=np.float32,
    )

    window = max(2, int(window))
    sequence_stride = max(1, int(sequence_stride))
    future_horizon_steps = max(1, int(future_horizon_steps))
    future_loss_weight = max(0.0, min(1.0, float(future_loss_weight)))
    input_dim = len(input_columns) * window + 4

    rng = np.random.default_rng(seed)
    params = _init_params(input_dim, hidden_dim, hidden_dim_2, rng)
    m = {key: np.zeros_like(value) for key, value in params.items()}
    v = {key: np.zeros_like(value) for key, value in params.items()}

    step = 0
    history: list[dict[str, Any]] = []
    kick_pos_weight = float(train_stats["kick_positive_weight"])
    best_score = -math.inf
    best_epoch = 0
    best_params = {key: value.copy() for key, value in params.items()}

    for epoch in range(1, max(1, epochs) + 1):
        losses: list[float] = []
        direction_losses: list[float] = []
        kick_losses: list[float] = []
        future_losses: list[float] = []
        batches = 0
        sequence_samples = 0

        for x, direction, kick, future_direction, _, sample_weight in _iter_batches(
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
            grads, loss = _train_batch(
                x,
                direction,
                kick,
                future_direction,
                sample_weight,
                params,
                kick_pos_weight=kick_pos_weight,
                future_loss_weight=future_loss_weight,
                l2=max(0.0, l2),
            )
            step += 1
            _adam_update(
                params,
                grads,
                m,
                v,
                step=step,
                learning_rate=learning_rate,
            )
            losses.append(loss["loss"])
            direction_losses.append(loss["direction_loss"])
            kick_losses.append(loss["kick_loss"])
            future_losses.append(loss["future_direction_loss"])
            batches += 1
            sequence_samples += len(direction)

        if batches == 0:
            raise ValueError(
                "elite training produced zero temporal sequences; "
                "reduce window or inspect shard continuity"
            )

        validation_metrics, _, _ = evaluate(
            validation_index,
            params=params,
            mean=mean,
            std=std,
            role_weights=role_weights,
            window=window,
            sequence_stride=sequence_stride,
            batch_size=max(32, batch_size),
            kick_threshold=0.5,
            future_horizon_steps=future_horizon_steps,
        )
        if int(validation_metrics.get("samples", 0)) <= 0:
            raise ValueError("validation split produced zero temporal sequences")

        # Model selection is validation-only. The frozen holdout is untouched
        # until all training and threshold selection are complete.
        validation_score = (
            float(validation_metrics["direction_accuracy"])
            + 0.25 * float(validation_metrics["kick_f1"])
            + 0.10 * float(validation_metrics["future_direction_accuracy"])
        )
        if validation_score > best_score:
            best_score = validation_score
            best_epoch = epoch
            best_params = {key: value.copy() for key, value in params.items()}

        epoch_record = {
            "epoch": epoch,
            "batches": batches,
            "sequence_samples": sequence_samples,
            "train_loss_mean": float(np.mean(losses)),
            "direction_loss_mean": float(np.mean(direction_losses)),
            "kick_loss_mean": float(np.mean(kick_losses)),
            "future_direction_loss_mean": float(np.mean(future_losses)),
            "validation_at_0_5": validation_metrics,
            "validation_selection_score": validation_score,
        }
        history.append(epoch_record)
        if progress_path is not None:
            _atomic_json(
                progress_path,
                {
                    "schema": "haxlab-elite-progress-v1",
                    "phase": "training",
                    "epoch": epoch,
                    "epochs_total": max(1, epochs),
                    "best_epoch": best_epoch,
                    "best_validation_selection_score": best_score,
                    "latest": epoch_record,
                },
            )

    params = best_params

    validation_default, _, _ = evaluate(
        validation_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=max(32, batch_size),
        kick_threshold=0.5,
        future_horizon_steps=future_horizon_steps,
    )
    kick_probs, kick_truth, kick_roles = _collect_kick_predictions(
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

    best_threshold, kick_calibration, threshold_candidates = (
        _calibrate_kick_threshold(
            kick_probs,
            kick_truth,
            max_rate_multiplier=1.5,
        )
    )
    best_kick_f1 = float(kick_calibration["f1"])

    kick_thresholds_by_role: dict[int, float] = {}
    kick_calibration_by_role: dict[str, dict[str, Any]] = {}
    for role_id in range(4):
        mask = kick_roles == role_id
        role_name = ROLE_NAMES[role_id]
        if int(mask.sum()) >= 100 and bool(kick_truth[mask].any()):
            role_threshold, role_calibration, role_candidates = (
                _calibrate_kick_threshold(
                    kick_probs[mask],
                    kick_truth[mask],
                    max_rate_multiplier=1.5,
                )
            )
            kick_thresholds_by_role[role_id] = role_threshold
            kick_calibration_by_role[role_name] = {
                **role_calibration,
                "samples": int(mask.sum()),
                "source": "role_validation",
                "candidates": role_candidates,
            }
        else:
            kick_thresholds_by_role[role_id] = best_threshold
            kick_calibration_by_role[role_name] = {
                **kick_calibration,
                "samples": int(mask.sum()),
                "source": "global_fallback",
                "candidates": threshold_candidates,
            }

    validation_final, _, _ = evaluate(
        validation_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=max(32, batch_size),
        kick_threshold=best_threshold,
        kick_thresholds_by_role=kick_thresholds_by_role,
        future_horizon_steps=future_horizon_steps,
    )

    # Frozen holdout: exactly one final evaluation after architecture/model
    # selection and threshold calibration have completed on train+validation.
    holdout_final, _, _ = evaluate(
        holdout_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=sequence_stride,
        batch_size=max(32, batch_size),
        kick_threshold=best_threshold,
        kick_thresholds_by_role=kick_thresholds_by_role,
        future_horizon_steps=future_horizon_steps,
    )
    if int(holdout_final.get("samples", 0)) <= 0:
        raise ValueError("frozen holdout produced zero temporal sequences")

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez_compressed(
        model_path,
        mean=mean,
        std=std,
        role_weights=role_weights,
        **params,
    )

    runtime_model_path = output_dir / "runtime-model.json"
    runtime_model = {
        "schema": "haxlab-elite-js-runtime-v1",
        "source_model_schema": MODEL_SCHEMA,
        "window": window,
        "base_input_columns": input_columns,
        "feature_ordering": "team-line-order-v1",
        "role_ids": {name: role_id for role_id, name in ROLE_NAMES.items()},
        "direction_classes": [
            {"class_id": i, "dir_x": dx, "dir_y": dy}
            for i, (dx, dy) in enumerate(ACTION_DIRS)
        ],
        "kick_threshold": float(best_threshold),
        "kick_thresholds_by_role": {
            ROLE_NAMES[role_id]: float(threshold)
            for role_id, threshold in kick_thresholds_by_role.items()
        },
        "kick_max_distance": RUNTIME_KICK_MAX_DISTANCE,
        "future_horizon_steps": future_horizon_steps,
        "future_deadzone": 8.0,
        "mean": mean.astype(float).tolist(),
        "std": std.astype(float).tolist(),
        "weights": {
            key: value.astype(float).tolist()
            for key, value in params.items()
        },
    }
    _atomic_json(runtime_model_path, runtime_model)

    metadata = {
        "schema": MODEL_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_path": str(model_path),
        "runtime_model_path": str(runtime_model_path),
        "train_index": str(train_index_path),
        "validation_index": str(validation_index_path),
        "holdout_index": str(holdout_index_path),
        "base_input_columns": input_columns,
        "excluded_input_columns": list(EXCLUDED_INPUT_COLUMNS),
        "role_ids": {name: role_id for role_id, name in ROLE_NAMES.items()},
        "direction_classes": [
            {"class_id": i, "dir_x": dx, "dir_y": dy}
            for i, (dx, dy) in enumerate(ACTION_DIRS)
        ],
        "runtime": {
            "kick_max_distance": RUNTIME_KICK_MAX_DISTANCE,
            "kick_gate": "euclidean_ball_distance",
        },
        "architecture": {
            "type": "numpy_temporal_mlp_role_conditioned",
            "window": window,
            "sequence_stride": sequence_stride,
            "frame_input_dim": len(input_columns),
            "role_embedding": "one_hot_4",
            "input_dim": input_dim,
            "hidden_dim": hidden_dim,
            "hidden_dim_2": hidden_dim_2,
            "direction_classes": 9,
            "kick_head": "binary_sigmoid",
            "future_direction_head": "9_way_displacement_direction",
            "future_horizon_steps": future_horizon_steps,
            "future_horizon_seconds": (
                future_horizon_steps
                * max(1, int(train_index.get("sample_every_ticks") or 6))
                / 60.0
            ),
        },
        "training": {
            "seed": seed,
            "epochs_requested": max(1, epochs),
            "best_epoch": best_epoch,
            "batch_size": max(32, batch_size),
            "learning_rate": learning_rate,
            "l2": max(0.0, l2),
            "future_loss_weight": future_loss_weight,
            "future_horizon_steps": future_horizon_steps,
            "train_replays": len(train_index["entries"]),
            "validation_replays": len(validation_index["entries"]),
            "holdout_replays": len(holdout_index["entries"]),
            "train_stats": train_stats,
            "role_balance_weights": {
                ROLE_NAMES[i]: float(role_weights[i]) for i in range(4)
            },
            "kick_threshold_source": "validation_only",
            "calibrated_kick_threshold": best_threshold,
            "validation_best_kick_f1": best_kick_f1,
            "kick_calibration": kick_calibration,
            "kick_calibration_by_role": kick_calibration_by_role,
            "calibrated_kick_thresholds_by_role": {
                ROLE_NAMES[role_id]: float(threshold)
                for role_id, threshold in kick_thresholds_by_role.items()
            },
            "kick_threshold_candidates": threshold_candidates,
            "frozen_holdout_used_for_selection": False,
        },
        "history": history,
        "validation_default_threshold": validation_default,
        "final_validation": validation_final,
        "final_holdout": holdout_final,
    }
    _atomic_json(output_dir / "metrics.json", metadata)
    if progress_path is not None:
        _atomic_json(
            progress_path,
            {
                "schema": "haxlab-elite-progress-v1",
                "phase": "training_complete",
                "best_epoch": best_epoch,
                "epochs_total": max(1, epochs),
                "final_validation": validation_final,
                "final_holdout": holdout_final,
                "model_path": str(model_path),
            },
        )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-train-elite")
    parser.add_argument("--train-index", type=Path, required=True)
    parser.add_argument("--validation-index", type=Path, required=True)
    parser.add_argument("--holdout-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--train-limit", type=int, default=None)
    parser.add_argument("--validation-limit", type=int, default=None)
    parser.add_argument("--holdout-limit", type=int, default=None)
    parser.add_argument("--window", type=int, default=8)
    parser.add_argument("--sequence-stride", type=int, default=1)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--hidden-dim-2", type=int, default=96)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--learning-rate", type=float, default=8e-4)
    parser.add_argument("--l2", type=float, default=1e-5)
    parser.add_argument("--future-horizon-steps", type=int, default=5)
    parser.add_argument("--future-loss-weight", type=float, default=0.35)
    parser.add_argument("--seed", type=int, default=1337)
    args = parser.parse_args()

    result = train_elite_policy(
        train_index_path=args.train_index,
        validation_index_path=args.validation_index,
        holdout_index_path=args.holdout_index,
        output_dir=args.output_dir,
        train_limit=args.train_limit,
        validation_limit=args.validation_limit,
        holdout_limit=args.holdout_limit,
        window=max(2, args.window),
        sequence_stride=max(1, args.sequence_stride),
        hidden_dim=max(16, args.hidden_dim),
        hidden_dim_2=max(16, args.hidden_dim_2),
        epochs=max(1, args.epochs),
        batch_size=max(32, args.batch_size),
        learning_rate=max(1e-6, args.learning_rate),
        l2=max(0.0, args.l2),
        future_horizon_steps=max(1, args.future_horizon_steps),
        future_loss_weight=max(0.0, min(1.0, args.future_loss_weight)),
        seed=args.seed,
    )
    print(json.dumps(result["final_holdout"], indent=2, sort_keys=True))
    print("model:", result["model_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())