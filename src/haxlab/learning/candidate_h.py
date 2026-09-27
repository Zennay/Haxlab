from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from haxlab.learning import elite


CONTRACT_SCHEMA = "haxlab-candidate-h-h1-gated-recovery-contract-v1"
RUNTIME_ROUTING_SCHEMA = "haxlab-learned-recovery-routing-v1"
MODEL_SCHEMA = "haxlab-candidate-h-h1-gated-recovery-v1"
CANDIDATE = "H"
ROLE_IDS = {name: role_id for role_id, name in elite.ROLE_NAMES.items()}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_blob_sha1(path: Path) -> str:
    raw = path.read_bytes()
    digest = hashlib.sha1()
    digest.update(f"blob {len(raw)}\0".encode("ascii"))
    digest.update(raw)
    return digest.hexdigest()


def _require_sha256(path: Path, expected: str, label: str) -> str:
    actual = _sha256_file(path)
    if actual != str(expected).lower():
        raise ValueError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def _load_contract(path: Path, expected_git_blob: str) -> dict[str, Any]:
    actual_blob = _git_blob_sha1(path)
    if actual_blob != str(expected_git_blob).lower():
        raise ValueError(
            "Candidate-H contract git blob mismatch: "
            f"expected {expected_git_blob}, got {actual_blob}"
        )
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("unsupported Candidate-H contract schema")
    if contract.get("candidate") != CANDIDATE or not contract.get("frozen_before_training"):
        raise ValueError("Candidate-H contract is not frozen")
    return contract


def _direction_component(delta: float, deadzone: float) -> int:
    if delta > deadzone:
        return 1
    if delta < -deadzone:
        return -1
    return 0


def _load_recovery_examples(
    path: Path,
    *,
    split: str,
    expected_count: int,
    input_columns: list[str],
    mean: np.ndarray,
    std: np.ndarray,
    window: int,
    failure_any_of: set[str],
    deadzone: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    rows: list[tuple[str, np.ndarray, int, int]] = []
    role_eye = np.eye(4, dtype=np.float32)
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("split") != split:
                raise ValueError(f"{path}:{line_no}: unexpected recovery split")
            failures = set(row.get("failure_types") or [])
            recovery_window = row.get("recovery_window") or []
            if len(recovery_window) != window or not (failure_any_of & failures):
                continue
            target = (row.get("auxiliary_targets") or {}).get("role_target")
            if not isinstance(target, dict):
                raise ValueError(f"{path}:{line_no}: eligible row missing role_target")
            role_name = str(row.get("role") or "").lower()
            if role_name not in ROLE_IDS:
                raise ValueError(f"{path}:{line_no}: invalid role {role_name!r}")
            raw_frames: list[list[float]] = []
            for frame in recovery_window:
                features = frame.get("features") or {}
                missing = [name for name in input_columns if name not in features]
                if missing:
                    raise ValueError(
                        f"{path}:{line_no}: recovery frame missing features {missing}"
                    )
                raw_frames.append([float(features[name]) for name in input_columns])
            raw = np.asarray(raw_frames, dtype=np.float32)
            normalized = ((raw - mean) / std).astype(np.float32, copy=False)
            flat = normalized.reshape(-1)
            role_id = ROLE_IDS[role_name]
            model_x = np.concatenate([flat, role_eye[role_id]], axis=0)
            final_features = recovery_window[-1].get("features") or {}
            dx = float(target["canonical_x"]) - float(final_features["own_x"])
            dy = float(target["y"]) - float(final_features["own_y"])
            direction = (
                _direction_component(dx, deadzone),
                _direction_component(dy, deadzone),
            )
            fingerprint = str(row.get("state_fingerprint") or "")
            if len(fingerprint) != 64:
                raise ValueError(f"{path}:{line_no}: invalid state fingerprint")
            rows.append((fingerprint, model_x, elite.DIR_TO_CLASS[direction], role_id))
    rows.sort(key=lambda item: item[0])
    if len(rows) != int(expected_count):
        raise ValueError(
            f"{split} recovery eligible count mismatch: "
            f"expected {expected_count}, got {len(rows)}"
        )
    if not rows:
        raise ValueError(f"{split} recovery split produced zero eligible examples")
    return (
        np.stack([row[1] for row in rows]).astype(np.float32, copy=False),
        np.asarray([row[2] for row in rows], dtype=np.int64),
        np.asarray([row[3] for row in rows], dtype=np.int64),
        [row[0] for row in rows],
    )


def _hidden1(x: np.ndarray, base: dict[str, np.ndarray]) -> np.ndarray:
    return np.maximum(x @ base["w1"] + base["b1"], 0.0)


def _hidden2_from_h1(h1: np.ndarray, base: dict[str, np.ndarray]) -> np.ndarray:
    return np.maximum(h1 @ base["w2"] + base["b2"], 0.0)


def _hidden2(x: np.ndarray, base: dict[str, np.ndarray]) -> np.ndarray:
    return _hidden2_from_h1(_hidden1(x, base), base)


def _recovery_draw_indices(population: int, draws: int, *, seed: int) -> np.ndarray:
    population = int(population)
    draws = int(draws)
    if population <= 0 or draws <= 0:
        raise ValueError("recovery population and draws must be positive")
    rng = np.random.default_rng(int(seed))
    chunks: list[np.ndarray] = []
    remaining = draws
    while remaining > 0:
        permutation = rng.permutation(population)
        take = min(remaining, population)
        chunks.append(permutation[:take])
        remaining -= take
    return np.concatenate(chunks).astype(np.int64, copy=False)


def _adam_head_update(
    weight: np.ndarray,
    bias: np.ndarray,
    grad_weight: np.ndarray,
    grad_bias: np.ndarray,
    state: dict[str, np.ndarray],
    *,
    step: int,
    learning_rate: float,
) -> None:
    beta1 = 0.9
    beta2 = 0.999
    epsilon = 1e-8
    for key, param, grad in (
        ("w", weight, grad_weight),
        ("b", bias, grad_bias),
    ):
        state["m_" + key] *= beta1
        state["m_" + key] += (1.0 - beta1) * grad
        state["v_" + key] *= beta2
        state["v_" + key] += (1.0 - beta2) * np.square(grad)
        m_hat = state["m_" + key] / (1.0 - beta1**step)
        v_hat = state["v_" + key] / (1.0 - beta2**step)
        param -= learning_rate * m_hat / (np.sqrt(v_hat) + epsilon)


def _train_recovery_head(
    h2: np.ndarray,
    direction: np.ndarray,
    *,
    initial_weight: np.ndarray,
    initial_bias: np.ndarray,
    epochs: int,
    batch_size: int,
    draws_per_epoch: int,
    updates_per_epoch: int,
    seed: int,
    learning_rate: float,
) -> tuple[np.ndarray, np.ndarray, list[dict[str, float]]]:
    if draws_per_epoch != batch_size * updates_per_epoch:
        raise ValueError("Candidate-H recovery draws/update contract mismatch")
    weight = initial_weight.astype(np.float32, copy=True)
    bias = initial_bias.astype(np.float32, copy=True)
    state = {
        "m_w": np.zeros_like(weight),
        "v_w": np.zeros_like(weight),
        "m_b": np.zeros_like(bias),
        "v_b": np.zeros_like(bias),
    }
    history: list[dict[str, float]] = []
    step = 0
    for epoch in range(1, int(epochs) + 1):
        indices = _recovery_draw_indices(
            len(h2), draws_per_epoch, seed=int(seed) + epoch
        )
        losses: list[float] = []
        updates = 0
        for start in range(0, len(indices), batch_size):
            batch_indices = indices[start : start + batch_size]
            if len(batch_indices) != batch_size:
                raise ValueError("partial recovery batch violates frozen contract")
            x = h2[batch_indices]
            y = direction[batch_indices]
            prob = elite._softmax(x @ weight + bias)
            eps = 1e-7
            loss = -float(
                np.log(np.clip(prob[np.arange(len(y)), y], eps, 1.0)).mean()
            )
            delta = prob
            delta[np.arange(len(y)), y] -= 1.0
            delta /= max(1, len(y))
            grad_weight = (x.T @ delta).astype(np.float32)
            grad_bias = delta.sum(axis=0).astype(np.float32)
            step += 1
            _adam_head_update(
                weight,
                bias,
                grad_weight,
                grad_bias,
                state,
                step=step,
                learning_rate=learning_rate,
            )
            losses.append(loss)
            updates += 1
        if updates != updates_per_epoch or len(indices) != draws_per_epoch:
            raise ValueError("Candidate-H recovery optimizer count drift")
        history.append(
            {
                "epoch": float(epoch),
                "updates": float(updates),
                "draws": float(len(indices)),
                "loss_mean": float(np.mean(losses)),
            }
        )
    return weight, bias, history


def _head_metrics(
    h2: np.ndarray,
    target: np.ndarray,
    weight: np.ndarray,
    bias: np.ndarray,
    roles: np.ndarray | None = None,
) -> dict[str, Any]:
    prob = elite._softmax(h2 @ weight + bias)
    pred = prob.argmax(axis=1)
    eps = 1e-7
    payload: dict[str, Any] = {
        "samples": int(len(target)),
        "accuracy": float((pred == target).mean()),
        "cross_entropy": -float(
            np.log(np.clip(prob[np.arange(len(target)), target], eps, 1.0)).mean()
        ),
    }
    if roles is not None:
        payload["by_role"] = {
            elite.ROLE_NAMES[role_id]: {
                "samples": int((roles == role_id).sum()),
                "accuracy": float(
                    (pred[roles == role_id] == target[roles == role_id]).mean()
                )
                if int((roles == role_id).sum())
                else None,
            }
            for role_id in range(4)
        }
    return payload


def _collect_role_matched_inputs(
    index_path: Path,
    counts: dict[int, int],
    *,
    mean: np.ndarray,
    std: np.ndarray,
    role_weights: np.ndarray,
    window: int,
    future_horizon_steps: int,
) -> dict[int, np.ndarray]:
    index = elite._load_index(index_path)
    rows: dict[int, list[np.ndarray]] = {role_id: [] for role_id in range(4)}
    totals = {role_id: 0 for role_id in range(4)}
    iterator = elite._iter_batches(
        index,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=1,
        batch_size=4096,
        rng=np.random.default_rng(1337),
        shuffle=False,
        future_horizon_steps=future_horizon_steps,
        state_jitter_std=0.0,
    )
    for x, _, _, _, roles, _ in iterator:
        for role_id in range(4):
            remaining = int(counts[role_id]) - totals[role_id]
            if remaining <= 0:
                continue
            selected = x[roles == role_id][:remaining]
            if len(selected):
                rows[role_id].append(selected.astype(np.float32, copy=False))
                totals[role_id] += len(selected)
        if all(totals[role_id] >= int(counts[role_id]) for role_id in range(4)):
            break
    if any(totals[role_id] != int(counts[role_id]) for role_id in range(4)):
        raise ValueError(
            f"insufficient canonical human negatives: got {totals}, expected {counts}"
        )
    return {
        role_id: np.concatenate(rows[role_id], axis=0)
        for role_id in range(4)
    }


def _fit_activation_head(
    positive_h2: np.ndarray,
    negative_h2: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.concatenate([positive_h2, negative_h2], axis=0)
    labels = np.concatenate(
        [
            np.ones(len(positive_h2), dtype=np.int64),
            np.zeros(len(negative_h2), dtype=np.int64),
        ]
    )
    design = np.c_[x, np.ones(len(x), dtype=np.float32)]
    target = np.eye(2, dtype=np.float32)[labels]
    coefficient = np.linalg.lstsq(design, target, rcond=None)[0]
    return (
        coefficient[:-1].astype(np.float32),
        coefficient[-1].astype(np.float32),
    )


def _activation_metrics(
    positive_h2: np.ndarray,
    negative_h2: np.ndarray,
    weight: np.ndarray,
    bias: np.ndarray,
    *,
    threshold: float,
) -> dict[str, Any]:
    positive_prob = elite._softmax(positive_h2 @ weight + bias)[:, 1]
    negative_prob = elite._softmax(negative_h2 @ weight + bias)[:, 1]
    positive_pred = positive_prob >= threshold
    negative_pred = negative_prob >= threshold
    return {
        "positive_samples": int(len(positive_h2)),
        "negative_samples": int(len(negative_h2)),
        "recovery_recall": float(positive_pred.mean()),
        "normal_specificity": float((~negative_pred).mean()),
        "false_activation_rate": float(negative_pred.mean()),
    }


def _route_direction_probabilities(
    base_prob: np.ndarray,
    recovery_prob: np.ndarray,
    activation_probability: np.ndarray,
    *,
    threshold: float,
) -> tuple[np.ndarray, np.ndarray]:
    active = np.asarray(activation_probability) >= float(threshold)
    routed = base_prob.copy()
    routed[active] = recovery_prob[active]
    return routed, active


def _routed_index_metrics(
    index_path: Path,
    *,
    base: dict[str, np.ndarray],
    input_columns: list[str],
    recovery_weight: np.ndarray,
    recovery_bias: np.ndarray,
    activation_weight: np.ndarray,
    activation_bias: np.ndarray,
    activation_threshold: float,
    window: int,
    future_horizon_steps: int,
    base_runtime: dict[str, Any],
) -> dict[str, Any]:
    index = elite._load_index(index_path)
    role_weights = base["role_weights"]
    kick_threshold = float(base_runtime["kick_threshold"])
    by_role = {
        ROLE_IDS[name]: float(value)
        for name, value in (base_runtime.get("kick_thresholds_by_role") or {}).items()
        if name in ROLE_IDS
    }
    samples = direction_correct = joint_correct = future_correct = active_count = 0
    kick_tp = kick_fp = kick_fn = kick_tn = 0
    iterator = elite._iter_batches(
        index,
        mean=base["mean"],
        std=base["std"],
        role_weights=role_weights,
        window=window,
        sequence_stride=1,
        batch_size=4096,
        rng=np.random.default_rng(1337),
        shuffle=False,
        future_horizon_steps=future_horizon_steps,
        state_jitter_std=0.0,
    )
    for x, direction, kick, future_direction, roles, _ in iterator:
        h1 = _hidden1(x, base)
        h2 = _hidden2_from_h1(h1, base)
        base_direction = elite._softmax(h2 @ base["wd"] + base["bd"])
        recovery_direction = elite._softmax(h2 @ recovery_weight + recovery_bias)
        activation = elite._softmax(h1 @ activation_weight + activation_bias)[:, 1]
        routed, active = _route_direction_probabilities(
            base_direction,
            recovery_direction,
            activation,
            threshold=activation_threshold,
        )
        direction_pred = routed.argmax(axis=1)
        future_pred = elite._softmax(h2 @ base["wf"] + base["bf"]).argmax(axis=1)
        kick_prob = elite._sigmoid(h2 @ base["wk"] + base["bk"]).reshape(-1)
        thresholds = np.asarray(
            [by_role.get(int(role), kick_threshold) for role in roles],
            dtype=np.float32,
        )
        kick_pred = kick_prob >= thresholds
        kick_true = kick > 0.5
        samples += len(direction)
        active_count += int(active.sum())
        direction_correct += int((direction_pred == direction).sum())
        future_correct += int((future_pred == future_direction).sum())
        joint_correct += int(
            ((direction_pred == direction) & (kick_pred == kick_true)).sum()
        )
        kick_tp += int((kick_pred & kick_true).sum())
        kick_fp += int((kick_pred & ~kick_true).sum())
        kick_fn += int((~kick_pred & kick_true).sum())
        kick_tn += int((~kick_pred & ~kick_true).sum())
    if samples <= 0:
        raise ValueError(f"{index_path}: routed evaluation produced zero samples")
    precision = kick_tp / max(1, kick_tp + kick_fp)
    recall = kick_tp / max(1, kick_tp + kick_fn)
    f1 = 2.0 * precision * recall / max(1e-12, precision + recall)
    return {
        "samples": samples,
        "direction_accuracy": direction_correct / samples,
        "joint_accuracy": joint_correct / samples,
        "kick_precision": precision,
        "kick_recall": recall,
        "kick_f1": f1,
        "kick_tp": kick_tp,
        "kick_fp": kick_fp,
        "kick_fn": kick_fn,
        "kick_tn": kick_tn,
        "future_direction_accuracy": future_correct / samples,
        "recovery_activation_rate": active_count / samples,
    }


def _runtime_payload(
    base_runtime: dict[str, Any],
    *,
    recovery_weight: np.ndarray,
    recovery_bias: np.ndarray,
    activation_weight: np.ndarray,
    activation_bias: np.ndarray,
    threshold: float,
    contract_blob: str,
) -> dict[str, Any]:
    payload = json.loads(json.dumps(base_runtime))
    weights = payload.setdefault("weights", {})
    weights["recovery_wd"] = recovery_weight.astype(float).tolist()
    weights["recovery_bd"] = recovery_bias.astype(float).tolist()
    weights["recovery_activation_w"] = activation_weight.astype(float).tolist()
    weights["recovery_activation_b"] = activation_bias.astype(float).tolist()
    payload["recovery_routing"] = {
        "schema": RUNTIME_ROUTING_SCHEMA,
        "candidate": CANDIDATE,
        "activation_representation": "h1",
        "recovery_direction_representation": "h2",
        "activation_threshold": float(threshold),
        "direction_only": True,
        "base_direction_exact_when_inactive": True,
        "kick_and_future_always_base": True,
        "contract_git_blob": contract_blob,
    }
    return payload


def _split_role_arrays(
    arrays: dict[int, np.ndarray],
    first_counts: dict[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    first: list[np.ndarray] = []
    second: list[np.ndarray] = []
    for role_id in range(4):
        cut = int(first_counts[role_id])
        first.append(arrays[role_id][:cut])
        second.append(arrays[role_id][cut:])
    return np.concatenate(first, axis=0), np.concatenate(second, axis=0)


def train_candidate_h(args: argparse.Namespace) -> dict[str, Any]:
    contract = _load_contract(args.contract, args.contract_git_blob)
    activation_cfg = contract["recovery_activation_head"]
    if activation_cfg.get("representation") != "h1":
        raise ValueError("Candidate-H activation representation must be frozen h1")
    if int(activation_cfg.get("representation_dim") or 0) != 128:
        raise ValueError("Candidate-H activation representation must be 128-dimensional h1")
    base_cfg = contract["base"]
    _require_sha256(args.base_model, base_cfg["model_sha256"], "Candidate-E model")
    _require_sha256(
        args.base_runtime,
        base_cfg["runtime_model_sha256"],
        "Candidate-E runtime model",
    )
    _require_sha256(
        args.base_metrics,
        base_cfg["metrics_sha256"],
        "Candidate-E metrics",
    )
    for split, path in (
        ("train", args.train_index),
        ("validation", args.validation_index),
        ("holdout", args.holdout_index),
    ):
        _require_sha256(
            path,
            contract["human_indexes"][f"{split}_sha256"],
            f"human {split} index",
        )
    _require_sha256(
        args.recovery_dir / "manifest.json",
        contract["recovery_dataset"]["manifest_sha256"],
        "recovery-v2 manifest",
    )
    bound_promotion_sha = str(
        contract["full_candidate_rules"]["promotion_v5_manifest_sha256"]
    ).lower()
    if str(args.promotion_manifest_sha256).lower() != bound_promotion_sha:
        raise ValueError(
            "Promotion-v5 CLI hash does not match frozen Candidate-H contract"
        )
    promotion_sha = _require_sha256(
        args.promotion_manifest,
        bound_promotion_sha,
        "Promotion-v5 manifest",
    )

    base_npz = np.load(args.base_model)
    base = {key: base_npz[key].copy() for key in base_npz.files}
    required_base = {
        "mean", "std", "role_weights", "w1", "b1", "w2", "b2",
        "wd", "bd", "wk", "bk", "wf", "bf",
    }
    missing = sorted(required_base - set(base))
    if missing:
        raise ValueError(f"Candidate-E model missing frozen parameters: {missing}")
    base_runtime = json.loads(args.base_runtime.read_text(encoding="utf-8"))
    base_metrics = json.loads(args.base_metrics.read_text(encoding="utf-8"))
    input_columns = list(base_metrics["base_input_columns"])
    window = int(base_cfg["window"])
    future_horizon_steps = int(
        (base_metrics.get("training") or {}).get("future_horizon_steps") or 5
    )

    recovery_cfg = contract["recovery_dataset"]
    eligible_counts = recovery_cfg["eligible_counts"]
    failure_any_of = set(recovery_cfg["eligible_filter"]["failure_any_of"])
    deadzone = float(contract["recovery_direction_head"]["target_deadzone"])
    recovery: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]] = {}
    for split in ("train", "validation", "evaluation"):
        recovery[split] = _load_recovery_examples(
            args.recovery_dir / f"{split}.jsonl",
            split=split,
            expected_count=int(eligible_counts[split]),
            input_columns=input_columns,
            mean=base["mean"],
            std=base["std"],
            window=window,
            failure_any_of=failure_any_of,
            deadzone=deadzone,
        )
    recovery_h1 = {
        split: _hidden1(recovery[split][0], base)
        for split in recovery
    }
    recovery_h2 = {
        split: _hidden2_from_h1(recovery_h1[split], base)
        for split in recovery
    }

    head_cfg = contract["recovery_direction_head"]
    recovery_weight, recovery_bias, recovery_history = _train_recovery_head(
        recovery_h2["train"],
        recovery["train"][1],
        initial_weight=base["wd"],
        initial_bias=base["bd"],
        epochs=int(head_cfg["epochs"]),
        batch_size=int(head_cfg["batch_size"]),
        draws_per_epoch=int(head_cfg["draws_per_epoch"]),
        updates_per_epoch=int(head_cfg["updates_per_epoch"]),
        seed=int(head_cfg["seed"]),
        learning_rate=float(head_cfg["learning_rate"]),
    )

    train_counts = {
        ROLE_IDS[name]: int(count)
        for name, count in contract["recovery_dataset"]["train_by_role"].items()
    }
    train_neg = _collect_role_matched_inputs(
        args.train_index,
        train_counts,
        mean=base["mean"],
        std=base["std"],
        role_weights=base["role_weights"],
        window=window,
        future_horizon_steps=future_horizon_steps,
    )
    train_negative_h1 = _hidden1(
        np.concatenate([train_neg[role_id] for role_id in range(4)], axis=0),
        base,
    )
    activation_weight, activation_bias = _fit_activation_head(
        recovery_h1["train"], train_negative_h1
    )

    val_role_counts = {
        role_id: int((recovery["validation"][2] == role_id).sum())
        for role_id in range(4)
    }
    eval_role_counts = {
        role_id: int((recovery["evaluation"][2] == role_id).sum())
        for role_id in range(4)
    }
    val_pool = _collect_role_matched_inputs(
        args.validation_index,
        {
            role_id: val_role_counts[role_id] + eval_role_counts[role_id]
            for role_id in range(4)
        },
        mean=base["mean"],
        std=base["std"],
        role_weights=base["role_weights"],
        window=window,
        future_horizon_steps=future_horizon_steps,
    )
    validation_neg_x, evaluation_neg_x = _split_role_arrays(
        val_pool, val_role_counts
    )
    validation_neg_h1 = _hidden1(validation_neg_x, base)
    evaluation_neg_h1 = _hidden1(evaluation_neg_x, base)
    validation_neg_h2 = _hidden2_from_h1(validation_neg_h1, base)
    evaluation_neg_h2 = _hidden2_from_h1(evaluation_neg_h1, base)
    threshold = float(contract["recovery_activation_head"]["threshold"])

    recovery_metrics = {
        split: _head_metrics(
            recovery_h2[split],
            recovery[split][1],
            recovery_weight,
            recovery_bias,
            recovery[split][2],
        )
        for split in ("train", "validation", "evaluation")
    }
    activation_metrics = {
        "train": _activation_metrics(
            recovery_h1["train"],
            train_negative_h1,
            activation_weight,
            activation_bias,
            threshold=threshold,
        ),
        "validation": _activation_metrics(
            recovery_h1["validation"],
            validation_neg_h1,
            activation_weight,
            activation_bias,
            threshold=threshold,
        ),
        "evaluation": _activation_metrics(
            recovery_h1["evaluation"],
            evaluation_neg_h1,
            activation_weight,
            activation_bias,
            threshold=threshold,
        ),
    }

    base_validation_prob = elite._softmax(
        validation_neg_h2 @ base["wd"] + base["bd"]
    )
    recovery_validation_prob = elite._softmax(
        validation_neg_h2 @ recovery_weight + recovery_bias
    )
    validation_activation = elite._softmax(
        validation_neg_h1 @ activation_weight + activation_bias
    )[:, 1]
    validation_routed, validation_active = _route_direction_probabilities(
        base_validation_prob,
        recovery_validation_prob,
        validation_activation,
        threshold=threshold,
    )
    off = ~validation_active
    gate_off_exact = bool(
        np.array_equal(validation_routed[off], base_validation_prob[off])
    )
    gate_off_max_delta = (
        float(np.max(np.abs(validation_routed[off] - base_validation_prob[off])))
        if off.any()
        else 0.0
    )

    smoke = {
        "recovery_head_validation_accuracy": recovery_metrics["validation"]["accuracy"],
        "recovery_head_evaluation_accuracy": recovery_metrics["evaluation"]["accuracy"],
        "validation_recovery_recall": activation_metrics["validation"]["recovery_recall"],
        "evaluation_recovery_recall": activation_metrics["evaluation"]["recovery_recall"],
        "validation_normal_specificity": activation_metrics["validation"]["normal_specificity"],
        "evaluation_normal_specificity": activation_metrics["evaluation"]["normal_specificity"],
        "gate_off_base_exact": gate_off_exact,
        "gate_off_max_abs_delta": gate_off_max_delta,
    }
    floors = head_cfg["preregistered_smoke_floor"]
    activation_floors = contract["recovery_activation_head"]["preregistered_smoke_floor"]
    smoke_pass = all(
        [
            smoke["recovery_head_validation_accuracy"]
            >= float(floors["validation_accuracy"]),
            smoke["recovery_head_evaluation_accuracy"]
            >= float(floors["evaluation_accuracy"]),
            smoke["validation_recovery_recall"]
            >= float(activation_floors["validation_recovery_recall"]),
            smoke["evaluation_recovery_recall"]
            >= float(activation_floors["evaluation_recovery_recall"]),
            smoke["validation_normal_specificity"]
            >= float(activation_floors["validation_normal_specificity"]),
            smoke["evaluation_normal_specificity"]
            >= float(activation_floors["evaluation_normal_specificity"]),
            gate_off_exact,
            gate_off_max_delta == 0.0,
        ]
    )
    if not smoke_pass:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        elite._atomic_json(
            args.output_dir / "progress.json",
            {
                "schema": MODEL_SCHEMA,
                "phase": "implementation_smoke_failed",
                "smoke": smoke,
            },
        )
        raise ValueError(f"Candidate-H implementation smoke failed: {smoke}")

    # Only after all development smoke gates pass do we touch the sealed holdout.
    final_validation = _routed_index_metrics(
        args.validation_index,
        base=base,
        input_columns=input_columns,
        recovery_weight=recovery_weight,
        recovery_bias=recovery_bias,
        activation_weight=activation_weight,
        activation_bias=activation_bias,
        activation_threshold=threshold,
        window=window,
        future_horizon_steps=future_horizon_steps,
        base_runtime=base_runtime,
    )
    final_holdout = _routed_index_metrics(
        args.holdout_index,
        base=base,
        input_columns=input_columns,
        recovery_weight=recovery_weight,
        recovery_bias=recovery_bias,
        activation_weight=activation_weight,
        activation_bias=activation_bias,
        activation_threshold=threshold,
        window=window,
        future_horizon_steps=future_horizon_steps,
        base_runtime=base_runtime,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    model_path = args.output_dir / "model.npz"
    np.savez_compressed(
        model_path,
        **base,
        recovery_wd=recovery_weight,
        recovery_bd=recovery_bias,
        recovery_activation_w=activation_weight,
        recovery_activation_b=activation_bias,
    )
    runtime_path = args.output_dir / "runtime-model.json"
    runtime = _runtime_payload(
        base_runtime,
        recovery_weight=recovery_weight,
        recovery_bias=recovery_bias,
        activation_weight=activation_weight,
        activation_bias=activation_bias,
        threshold=threshold,
        contract_blob=args.contract_git_blob,
    )
    elite._atomic_json(runtime_path, runtime)

    metadata = {
        "schema": MODEL_SCHEMA,
        "candidate": CANDIDATE,
        "contract_path": str(args.contract),
        "contract_git_blob": args.contract_git_blob,
        "promotion_v5_manifest": str(args.promotion_manifest),
        "promotion_v5_manifest_sha256": promotion_sha,
        "base": {
            "model_path": str(args.base_model),
            "model_sha256": _sha256_file(args.base_model),
            "runtime_model_sha256": _sha256_file(args.base_runtime),
            "metrics_sha256": _sha256_file(args.base_metrics),
            "frozen_parameter_names": sorted(required_base),
        },
        "recovery_training_history": recovery_history,
        "recovery_metrics": recovery_metrics,
        "activation_metrics": activation_metrics,
        "implementation_smoke": {**smoke, "passed": True},
        "routing": {
            "activation_threshold": threshold,
            "gate_off_base_exact": gate_off_exact,
            "gate_off_max_abs_delta": gate_off_max_delta,
            "kick_and_future_always_base": True,
        },
        "final_validation": final_validation,
        "final_holdout": final_holdout,
        "model_path": str(model_path),
        "runtime_model_path": str(runtime_path),
    }
    elite._atomic_json(args.output_dir / "metrics.json", metadata)
    elite._atomic_json(
        args.output_dir / "progress.json",
        {
            "schema": MODEL_SCHEMA,
            "phase": "training_complete",
            "recovery_epochs": int(head_cfg["epochs"]),
            "recovery_updates_per_epoch": int(head_cfg["updates_per_epoch"]),
            "recovery_draws_per_epoch": int(head_cfg["draws_per_epoch"]),
            "smoke": {**smoke, "passed": True},
            "final_validation": final_validation,
            "final_holdout": final_holdout,
            "model_path": str(model_path),
            "runtime_model_path": str(runtime_path),
        },
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-candidate-g")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--contract-git-blob", required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--base-runtime", type=Path, required=True)
    parser.add_argument("--base-metrics", type=Path, required=True)
    parser.add_argument("--train-index", type=Path, required=True)
    parser.add_argument("--validation-index", type=Path, required=True)
    parser.add_argument("--holdout-index", type=Path, required=True)
    parser.add_argument("--recovery-dir", type=Path, required=True)
    parser.add_argument("--promotion-manifest", type=Path, required=True)
    parser.add_argument("--promotion-manifest-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metadata = train_candidate_h(args)
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
