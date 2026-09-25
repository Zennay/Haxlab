from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


MODEL_SCHEMA = "haxlab-bc-baseline-v1"
PORTABLE_MODEL_SCHEMA = "haxlab-bc-portable-v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
ACTION_DIRS = tuple(
    (dx, dy)
    for dy in (-1, 0, 1)
    for dx in (-1, 0, 1)
)
DIR_TO_CLASS = {direction: index for index, direction in enumerate(ACTION_DIRS)}
LABEL_COLUMNS = ("dir_x", "dir_y", "kick")
EXCLUDED_INPUT_COLUMNS = ("frame", "player_index", "team_id", *LABEL_COLUMNS)


def direction_class(dir_x: float, dir_y: float) -> int:
    dx = max(-1, min(1, int(round(float(dir_x)))))
    dy = max(-1, min(1, int(round(float(dir_y)))))
    return DIR_TO_CLASS[(dx, dy)]


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


def _load_index(index_path: Path, limit: int | None = None) -> dict[str, Any]:
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    entries = list(payload.get("entries") or [])
    if limit is not None:
        entries = entries[: max(0, limit)]
    return {**payload, "entries": entries}


def _load_shard(entry: dict[str, Any]) -> tuple[np.ndarray, list[str]]:
    shard_path = Path(str(entry["shard_path"]))
    meta_path = shard_path.with_suffix("").with_suffix(".meta.json")
    if not meta_path.exists():
        # .f32.gz -> .meta.json
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
    rows = values.reshape(-1, row_width)
    return rows, columns


def _column_layout(columns: list[str]) -> tuple[list[int], int, int, int]:
    by_name = {name: index for index, name in enumerate(columns)}
    missing = [name for name in LABEL_COLUMNS if name not in by_name]
    if missing:
        raise ValueError(f"missing label columns: {missing}")

    input_indices = [
        index
        for index, name in enumerate(columns)
        if name not in EXCLUDED_INPUT_COLUMNS
    ]
    return (
        input_indices,
        by_name["dir_x"],
        by_name["dir_y"],
        by_name["kick"],
    )


def _extract_xy(
    rows: np.ndarray,
    columns: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    input_indices, dx_i, dy_i, kick_i = _column_layout(columns)
    input_columns = [columns[i] for i in input_indices]
    x = rows[:, input_indices].astype(np.float32, copy=False)

    dx = np.clip(np.rint(rows[:, dx_i]), -1, 1).astype(np.int8)
    dy = np.clip(np.rint(rows[:, dy_i]), -1, 1).astype(np.int8)
    direction = ((dy + 1) * 3 + (dx + 1)).astype(np.int64)
    kick = (rows[:, kick_i] > 0.5).astype(np.float32)
    return x, direction, kick, input_columns


def _normalization(
    index: dict[str, Any],
) -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    count = 0
    total: np.ndarray | None = None
    total_sq: np.ndarray | None = None
    input_columns: list[str] | None = None
    direction_counts = np.zeros(9, dtype=np.int64)
    kick_positive = 0

    for entry in index["entries"]:
        rows, columns = _load_shard(entry)
        x, direction, kick, shard_columns = _extract_xy(rows, columns)
        if input_columns is None:
            input_columns = shard_columns
            total = np.zeros(x.shape[1], dtype=np.float64)
            total_sq = np.zeros(x.shape[1], dtype=np.float64)
        elif shard_columns != input_columns:
            raise ValueError("inconsistent shard column layout")

        assert total is not None and total_sq is not None
        total += x.sum(axis=0, dtype=np.float64)
        total_sq += np.square(x, dtype=np.float64).sum(axis=0, dtype=np.float64)
        count += x.shape[0]
        direction_counts += np.bincount(direction, minlength=9)
        kick_positive += int(kick.sum())

    if count <= 0 or total is None or total_sq is None or input_columns is None:
        raise ValueError("training index contains no samples")

    mean = total / count
    variance = np.maximum(total_sq / count - np.square(mean), 1e-8)
    std = np.sqrt(variance)
    std = np.where(std < 1e-4, 1.0, std)

    kick_negative = count - kick_positive
    kick_pos_weight = (
        min(25.0, max(1.0, kick_negative / max(1, kick_positive)))
        if kick_positive
        else 1.0
    )

    stats = {
        "samples": count,
        "direction_counts": direction_counts.tolist(),
        "kick_positive": kick_positive,
        "kick_negative": kick_negative,
        "kick_positive_rate": kick_positive / count,
        "kick_positive_weight": kick_pos_weight,
    }
    return (
        mean.astype(np.float32),
        std.astype(np.float32),
        input_columns,
        stats,
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
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    scale1 = math.sqrt(2.0 / max(1, input_dim))
    scale2 = math.sqrt(2.0 / max(1, hidden_dim))
    return {
        "w1": rng.normal(0.0, scale1, (input_dim, hidden_dim)).astype(np.float32),
        "b1": np.zeros(hidden_dim, dtype=np.float32),
        "wd": rng.normal(0.0, scale2, (hidden_dim, 9)).astype(np.float32),
        "bd": np.zeros(9, dtype=np.float32),
        "wk": rng.normal(0.0, scale2, (hidden_dim, 1)).astype(np.float32),
        "bk": np.zeros(1, dtype=np.float32),
    }


def _forward(
    x: np.ndarray,
    params: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    pre = x @ params["w1"] + params["b1"]
    hidden = np.maximum(pre, 0.0)
    dir_prob = _softmax(hidden @ params["wd"] + params["bd"])
    kick_prob = _sigmoid(hidden @ params["wk"] + params["bk"]).reshape(-1)
    return hidden, dir_prob, kick_prob


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
    params: dict[str, np.ndarray],
    *,
    kick_pos_weight: float,
    l2: float,
    sample_weight: np.ndarray | None = None,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    batch = x.shape[0]
    hidden, dir_prob, kick_prob = _forward(x, params)
    if sample_weight is None:
        sample_weight = np.ones(batch, dtype=np.float32)
    else:
        sample_weight = np.asarray(sample_weight, dtype=np.float32).reshape(-1)
        if sample_weight.shape[0] != batch:
            raise ValueError("sample_weight length must match batch")
        sample_weight = np.clip(sample_weight, 0.05, 5.0)
    sample_weight_sum = max(1e-6, float(sample_weight.sum()))

    eps = 1e-7
    dir_nll = -np.log(
        np.clip(dir_prob[np.arange(batch), direction], eps, 1.0)
    )
    dir_loss = float((dir_nll * sample_weight).sum() / sample_weight_sum)

    kick_weights = (
        np.where(kick > 0.5, kick_pos_weight, 1.0).astype(np.float32)
        * sample_weight
    )
    kick_loss = -(
        kick_weights
        * (
            kick * np.log(np.clip(kick_prob, eps, 1.0))
            + (1.0 - kick) * np.log(np.clip(1.0 - kick_prob, eps, 1.0))
        )
    ).sum() / max(1.0, float(kick_weights.sum()))

    ddir = dir_prob.copy()
    ddir[np.arange(batch), direction] -= 1.0
    ddir *= (sample_weight / sample_weight_sum).reshape(-1, 1)

    # Weighted BCE derivative, normalized by total sample weight.
    dkick = (
        (kick_prob - kick)
        * kick_weights
        / max(1.0, float(kick_weights.sum()))
    ).reshape(-1, 1)

    grad_wd = hidden.T @ ddir + l2 * params["wd"]
    grad_bd = ddir.sum(axis=0)
    grad_wk = hidden.T @ dkick + l2 * params["wk"]
    grad_bk = dkick.sum(axis=0)

    dhidden = ddir @ params["wd"].T + dkick @ params["wk"].T
    pre_active = hidden > 0.0
    dpre = dhidden * pre_active

    grad_w1 = x.T @ dpre + l2 * params["w1"]
    grad_b1 = dpre.sum(axis=0)

    grads = {
        "w1": grad_w1.astype(np.float32),
        "b1": grad_b1.astype(np.float32),
        "wd": grad_wd.astype(np.float32),
        "bd": grad_bd.astype(np.float32),
        "wk": grad_wk.astype(np.float32),
        "bk": grad_bk.astype(np.float32),
    }
    return grads, {
        "direction_loss": float(dir_loss),
        "kick_loss": float(kick_loss),
        "loss": float(dir_loss + kick_loss),
    }


def _iter_batches(
    index: dict[str, Any],
    *,
    mean: np.ndarray,
    std: np.ndarray,
    batch_size: int,
    rng: np.random.Generator,
    shuffle: bool,
    include_sample_weight: bool = False,
):
    entries = list(index["entries"])
    if shuffle:
        rng.shuffle(entries)

    for entry in entries:
        rows, columns = _load_shard(entry)
        x, direction, kick, _ = _extract_xy(rows, columns)
        x = ((x - mean) / std).astype(np.float32, copy=False)
        order = np.arange(x.shape[0])
        if shuffle:
            rng.shuffle(order)

        replay_weight = float(entry.get("example_weight", 1.0))
        for start in range(0, x.shape[0], batch_size):
            idx = order[start : start + batch_size]
            if include_sample_weight:
                weights = np.full(idx.shape[0], replay_weight, dtype=np.float32)
                yield x[idx], direction[idx], kick[idx], weights
            else:
                yield x[idx], direction[idx], kick[idx]


def evaluate(
    index: dict[str, Any],
    *,
    params: dict[str, np.ndarray],
    mean: np.ndarray,
    std: np.ndarray,
    batch_size: int = 8192,
) -> dict[str, Any]:
    total = 0
    direction_correct = 0
    joint_correct = 0
    kick_tp = 0
    kick_fp = 0
    kick_fn = 0
    kick_tn = 0
    direction_counts = np.zeros(9, dtype=np.int64)
    direction_confusion = np.zeros((9, 9), dtype=np.int64)

    rng = np.random.default_rng(0)
    for x, direction, kick in _iter_batches(
        index,
        mean=mean,
        std=std,
        batch_size=batch_size,
        rng=rng,
        shuffle=False,
        include_sample_weight=False,
    ):
        _, dir_prob, kick_prob = _forward(x, params)
        dir_pred = dir_prob.argmax(axis=1)
        kick_pred = kick_prob >= 0.5
        kick_true = kick > 0.5

        total += x.shape[0]
        direction_correct += int((dir_pred == direction).sum())
        joint_correct += int(
            ((dir_pred == direction) & (kick_pred == kick_true)).sum()
        )
        kick_tp += int((kick_pred & kick_true).sum())
        kick_fp += int((kick_pred & ~kick_true).sum())
        kick_fn += int((~kick_pred & kick_true).sum())
        kick_tn += int((~kick_pred & ~kick_true).sum())
        direction_counts += np.bincount(direction, minlength=9)
        np.add.at(direction_confusion, (direction, dir_pred), 1)

    if total <= 0:
        raise ValueError("evaluation index contains no samples")

    kick_precision = kick_tp / max(1, kick_tp + kick_fp)
    kick_recall = kick_tp / max(1, kick_tp + kick_fn)
    kick_f1 = (
        2.0 * kick_precision * kick_recall
        / max(1e-12, kick_precision + kick_recall)
    )
    majority_direction = int(direction_counts.max())
    no_kick = kick_tn + kick_fp
    direction_recall_by_class = []
    for class_id in range(9):
        support = int(direction_confusion[class_id].sum())
        recall = (
            float(direction_confusion[class_id, class_id]) / support
            if support > 0
            else None
        )
        direction_recall_by_class.append(recall)
    supported_recalls = [
        recall for recall in direction_recall_by_class if recall is not None
    ]
    direction_macro_recall = (
        float(np.mean(supported_recalls)) if supported_recalls else 0.0
    )

    return {
        "samples": total,
        "direction_accuracy": direction_correct / total,
        "direction_macro_recall": direction_macro_recall,
        "direction_recall_by_class": direction_recall_by_class,
        "direction_confusion": direction_confusion.tolist(),
        "joint_accuracy": joint_correct / total,
        "kick_precision": kick_precision,
        "kick_recall": kick_recall,
        "kick_f1": kick_f1,
        "kick_true_rate": (kick_tp + kick_fn) / total,
        "kick_predicted_rate": (kick_tp + kick_fp) / total,
        "kick_confusion": {
            "tp": kick_tp,
            "fp": kick_fp,
            "fn": kick_fn,
            "tn": kick_tn,
        },
        "baselines": {
            "majority_direction_accuracy": majority_direction / total,
            "always_no_kick_accuracy": no_kick / total,
        },
    }


def train_baseline(
    *,
    train_index_path: Path,
    holdout_index_path: Path,
    output_dir: Path,
    train_limit: int | None = None,
    holdout_limit: int | None = None,
    hidden_dim: int = 64,
    epochs: int = 3,
    batch_size: int = 4096,
    learning_rate: float = 1e-3,
    l2: float = 1e-5,
    seed: int = 1337,
    use_example_weights: bool = False,
) -> dict[str, Any]:
    train_index = _load_index(train_index_path, train_limit)
    holdout_index = _load_index(holdout_index_path, holdout_limit)
    mean, std, input_columns, train_stats = _normalization(train_index)

    rng = np.random.default_rng(seed)
    params = _init_params(len(input_columns), hidden_dim, rng)
    m = {key: np.zeros_like(value) for key, value in params.items()}
    v = {key: np.zeros_like(value) for key, value in params.items()}

    step = 0
    history: list[dict[str, Any]] = []
    kick_pos_weight = float(train_stats["kick_positive_weight"])

    for epoch in range(1, max(1, epochs) + 1):
        losses: list[float] = []
        dir_losses: list[float] = []
        kick_losses: list[float] = []

        for batch_data in _iter_batches(
            train_index,
            mean=mean,
            std=std,
            batch_size=max(32, batch_size),
            rng=rng,
            shuffle=True,
            include_sample_weight=use_example_weights,
        ):
            if use_example_weights:
                x, direction, kick, sample_weight = batch_data
            else:
                x, direction, kick = batch_data
                sample_weight = None
            grads, loss = _train_batch(
                x,
                direction,
                kick,
                params,
                kick_pos_weight=kick_pos_weight,
                l2=max(0.0, l2),
                sample_weight=sample_weight,
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
            dir_losses.append(loss["direction_loss"])
            kick_losses.append(loss["kick_loss"])

        holdout_metrics = evaluate(
            holdout_index,
            params=params,
            mean=mean,
            std=std,
            batch_size=max(32, batch_size),
        )
        history.append(
            {
                "epoch": epoch,
                "train_loss_mean": float(np.mean(losses)),
                "direction_loss_mean": float(np.mean(dir_losses)),
                "kick_loss_mean": float(np.mean(kick_losses)),
                "holdout": holdout_metrics,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez_compressed(
        model_path,
        mean=mean,
        std=std,
        **params,
    )

    portable_path = output_dir / "model.portable.json"
    _atomic_json(
        portable_path,
        {
            "schema": PORTABLE_MODEL_SCHEMA,
            "input_columns": input_columns,
            "direction_classes": [
                {"class_id": i, "dir_x": dx, "dir_y": dy}
                for i, (dx, dy) in enumerate(ACTION_DIRS)
            ],
            "mean": mean.tolist(),
            "std": std.tolist(),
            "w1": params["w1"].tolist(),
            "b1": params["b1"].tolist(),
            "wd": params["wd"].tolist(),
            "bd": params["bd"].tolist(),
            "wk": params["wk"].reshape(-1).tolist(),
            "bk": params["bk"].reshape(-1).tolist(),
        },
    )

    final_metrics = history[-1]["holdout"]
    metadata = {
        "schema": MODEL_SCHEMA,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_path": str(model_path),
        "portable_model_path": str(portable_path),
        "artifact_hashes": {
            "model_sha256": _sha256_file(model_path),
            "portable_model_sha256": _sha256_file(portable_path),
            "train_index_sha256": _sha256_file(train_index_path),
            "holdout_index_sha256": _sha256_file(holdout_index_path),
        },
        "train_index": str(train_index_path),
        "holdout_index": str(holdout_index_path),
        "input_columns": input_columns,
        "excluded_input_columns": list(EXCLUDED_INPUT_COLUMNS),
        "direction_classes": [
            {"class_id": i, "dir_x": dx, "dir_y": dy}
            for i, (dx, dy) in enumerate(ACTION_DIRS)
        ],
        "architecture": {
            "type": "numpy_mlp_multitask",
            "input_dim": len(input_columns),
            "hidden_dim": hidden_dim,
            "direction_classes": 9,
            "kick_head": "binary_sigmoid",
        },
        "training": {
            "seed": seed,
            "epochs": max(1, epochs),
            "batch_size": max(32, batch_size),
            "learning_rate": learning_rate,
            "l2": max(0.0, l2),
            "train_replays": len(train_index["entries"]),
            "holdout_replays": len(holdout_index["entries"]),
            "train_stats": train_stats,
            "use_example_weights": bool(use_example_weights),
        },
        "history": history,
        "final_holdout": final_metrics,
    }
    _atomic_json(output_dir / "metrics.json", metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-train-bc")
    parser.add_argument(
        "--train-index",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--holdout-index",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
    )
    parser.add_argument("--train-limit", type=int, default=None)
    parser.add_argument("--holdout-limit", type=int, default=None)
    parser.add_argument("--hidden-dim", type=int, default=64)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--l2", type=float, default=1e-5)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--use-example-weights", action="store_true")
    args = parser.parse_args()

    result = train_baseline(
        train_index_path=args.train_index,
        holdout_index_path=args.holdout_index,
        output_dir=args.output_dir,
        train_limit=args.train_limit,
        holdout_limit=args.holdout_limit,
        hidden_dim=max(8, args.hidden_dim),
        epochs=max(1, args.epochs),
        batch_size=max(32, args.batch_size),
        learning_rate=max(1e-6, args.learning_rate),
        l2=max(0.0, args.l2),
        seed=args.seed,
        use_example_weights=args.use_example_weights,
    )
    print(json.dumps(result["final_holdout"], indent=2, sort_keys=True))
    print("model:", result["model_path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
