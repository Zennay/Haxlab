from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from haxlab.evaluation.elite_gate_preflight import (
    assert_development_evaluator_gate_compatible,
)
from haxlab.learning import elite
from haxlab.learning.touch_progression import OUTPUT_SCHEMA


CONTRACT_SCHEMA = "haxlab-candidate-i-touch-progression-multitask-contract-v1"
MODEL_SCHEMA = "haxlab-elite-temporal-policy-v1"
CANDIDATE = "I"
EXPECTED_HUMAN_BATCHES_PER_EPOCH = 5675
EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH = 8_313_339
ROLE_NAMES = {0: "gk", 1: "dm", 2: "am", 3: "st"}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(path: Path, expected: str, label: str) -> str:
    actual = _sha256_file(path)
    if actual != str(expected):
        raise ValueError(
            f"{label} SHA-256 mismatch: expected {expected}, got {actual}"
        )
    return actual


def _load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != CONTRACT_SCHEMA:
        raise ValueError("unsupported Candidate-I contract schema")
    if payload.get("candidate") != CANDIDATE:
        raise ValueError("Candidate-I contract candidate mismatch")
    if payload.get("frozen_before_training") is not True:
        raise ValueError("Candidate-I contract is not frozen before training")
    return payload


def _load_progression_manifest(
    path: Path,
    contract: dict[str, Any],
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != OUTPUT_SCHEMA:
        raise ValueError("unsupported Candidate-I materialized dataset schema")
    if payload.get("holdout_materialized") is not False:
        raise ValueError("Candidate-I progression holdout must not be materialized")

    expected = contract["progression_dataset"]
    for split, row_key, digest_key in (
        ("train", "train_rows", "train_row_digest_sha256"),
        ("validation", "validation_rows", "validation_row_digest_sha256"),
    ):
        actual = payload.get(split) or {}
        if int(actual.get("rows", -1)) != int(expected[row_key]):
            raise ValueError(f"{split} progression row count mismatch")
        if str(actual.get("row_fingerprint_digest_sha256")) != str(
            expected[digest_key]
        ):
            raise ValueError(f"{split} progression row digest mismatch")

    overlap = payload.get("split_overlap_replays") or {}
    expected_overlap = {
        "train_validation": int(expected["train_validation_replay_overlap"]),
        "train_holdout": int(expected["train_holdout_replay_overlap"]),
        "validation_holdout": int(expected["validation_holdout_replay_overlap"]),
    }
    if overlap != expected_overlap or any(int(value) for value in overlap.values()):
        raise ValueError(
            f"Candidate-I progression replay-overlap gate failed: {overlap}"
        )
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("schema") != "haxlab-candidate-i-touch-progression-row-v1":
                raise ValueError(f"{path}:{line_number}: unsupported row schema")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path}: progression dataset contains no rows")
    return rows


def _index_entries_by_replay(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for entry in index["entries"]:
        name = Path(str(entry["shard_path"])).name
        if not name.endswith(".f32.gz"):
            raise ValueError(f"unsupported elite shard name: {name}")
        replay_sha = name.removesuffix(".f32.gz").lower()
        if replay_sha in result:
            raise ValueError(f"duplicate replay in elite index: {replay_sha}")
        result[replay_sha] = entry
    return result


def _progression_examples(
    jsonl_path: Path,
    *,
    index_path: Path,
    mean: np.ndarray,
    std: np.ndarray,
    window: int,
    max_gap_ticks: int,
) -> tuple[np.ndarray, np.ndarray, list[str], np.ndarray]:
    rows = _read_jsonl(jsonl_path)
    index = elite._load_index(index_path)
    entries = _index_entries_by_replay(index)
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for position, row in enumerate(rows):
        grouped[str(row["source_replay_sha256"]).lower()].append((position, row))

    model_rows: list[np.ndarray | None] = [None] * len(rows)
    labels = np.zeros(len(rows), dtype=np.int64)
    roles = np.zeros(len(rows), dtype=np.int64)
    fingerprints = [""] * len(rows)
    role_eye = np.eye(4, dtype=np.float32)

    for replay_sha, replay_rows in grouped.items():
        entry = entries.get(replay_sha)
        if entry is None:
            raise ValueError(
                f"{jsonl_path}: replay {replay_sha} missing from human index"
            )
        shard, columns = elite._load_shard(entry)
        arrays = elite._extract_frame_arrays(shard, columns)
        normalized = ((arrays["x"] - mean) / std).astype(np.float32, copy=False)

        by_player: dict[int, tuple[np.ndarray, dict[int, int]]] = {}
        for player in np.unique(arrays["player"]):
            player_id = int(player)
            idx = np.flatnonzero(arrays["player"] == player)
            order = np.argsort(arrays["frame"][idx], kind="stable")
            idx = idx[order]
            frame_positions: dict[int, int] = {}
            for sequence_position, frame in enumerate(arrays["frame"][idx]):
                frame_int = int(frame)
                if frame_int in frame_positions:
                    raise ValueError(
                        f"{replay_sha}: duplicate frame {frame_int} for player {player_id}"
                    )
                frame_positions[frame_int] = sequence_position
            by_player[player_id] = (idx, frame_positions)

        for output_position, row in replay_rows:
            player_id = int(row["player_index"])
            sample_frame = int(row["sample_frame"])
            role_id = int(row["role_id"])
            if role_id not in ROLE_NAMES:
                raise ValueError(f"{replay_sha}: invalid progression role {role_id}")
            player_payload = by_player.get(player_id)
            if player_payload is None:
                raise ValueError(
                    f"{replay_sha}: progression player {player_id} missing from shard"
                )
            idx, frame_positions = player_payload
            endpoint_position = frame_positions.get(sample_frame)
            if endpoint_position is None:
                raise ValueError(
                    f"{replay_sha}: sample frame {sample_frame} missing for player {player_id}"
                )
            start = endpoint_position - (window - 1)
            if start < 0:
                raise ValueError(
                    f"{replay_sha}: progression row lacks {window}-frame history"
                )
            sequence_idx = idx[start : endpoint_position + 1]
            frames = arrays["frame"][sequence_idx]
            if len(sequence_idx) != window:
                raise ValueError(f"{replay_sha}: progression window cardinality mismatch")
            if len(frames) > 1 and int(np.max(np.diff(frames))) > int(max_gap_ticks):
                raise ValueError(
                    f"{replay_sha}: progression window exceeds max contiguous gap"
                )
            actual_role = int(arrays["role"][idx[endpoint_position]])
            if actual_role != role_id:
                raise ValueError(
                    f"{replay_sha}: progression role mismatch {role_id} != {actual_role}"
                )
            sequence = normalized[sequence_idx].reshape(-1)
            model_x = np.concatenate([sequence, role_eye[role_id]], axis=0)
            model_rows[output_position] = model_x.astype(np.float32, copy=False)
            labels[output_position] = int(row["positive_progression"])
            roles[output_position] = role_id
            fingerprints[output_position] = str(row["row_fingerprint"])

    if any(value is None for value in model_rows):
        raise ValueError(f"{jsonl_path}: failed to reconstruct all progression rows")
    x = np.stack([value for value in model_rows if value is not None], axis=0)
    return x.astype(np.float32, copy=False), labels, fingerprints, roles


def _hidden2(x: np.ndarray, params: dict[str, np.ndarray]) -> np.ndarray:
    h1 = np.maximum(x @ params["w1"] + params["b1"], 0.0)
    return np.maximum(h1 @ params["w2"] + params["b2"], 0.0)


def _binary_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    y = np.asarray(labels, dtype=np.int64).reshape(-1)
    s = np.asarray(scores, dtype=np.float64).reshape(-1)
    positive = int((y == 1).sum())
    negative = int((y == 0).sum())
    if positive <= 0 or negative <= 0:
        raise ValueError("AUC requires both positive and negative labels")

    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), dtype=np.float64)
    start = 0
    while start < len(order):
        end = start + 1
        value = s[order[start]]
        while end < len(order) and s[order[end]] == value:
            end += 1
        average_rank = 0.5 * ((start + 1) + end)
        ranks[order[start:end]] = average_rank
        start = end
    rank_sum = float(ranks[y == 1].sum())
    return (
        rank_sum - positive * (positive + 1) / 2.0
    ) / float(positive * negative)


def _linear_probe(
    train_h2: np.ndarray,
    train_labels: np.ndarray,
    validation_h2: np.ndarray,
    validation_labels: np.ndarray,
) -> dict[str, float]:
    design_train = np.c_[
        train_h2.astype(np.float64),
        np.ones(len(train_h2), dtype=np.float64),
    ]
    target = np.eye(2, dtype=np.float64)[train_labels.astype(np.int64)]
    coefficient = np.linalg.lstsq(design_train, target, rcond=None)[0]
    design_validation = np.c_[
        validation_h2.astype(np.float64),
        np.ones(len(validation_h2), dtype=np.float64),
    ]
    score = design_validation @ coefficient
    prediction = np.argmax(score, axis=1)
    return {
        "accuracy": float((prediction == validation_labels).mean()),
        "auc": _binary_auc(validation_labels, score[:, 1]),
        "majority_accuracy": float(
            max(
                (validation_labels == 0).mean(),
                (validation_labels == 1).mean(),
            )
        ),
    }


def _progression_draw_indices(
    *,
    row_count: int,
    seed: int,
    epoch: int,
    draws_per_epoch: int,
) -> np.ndarray:
    if row_count < draws_per_epoch:
        raise ValueError(
            f"Candidate-I progression rows {row_count} < frozen draws {draws_per_epoch}"
        )
    rng = np.random.default_rng(int(seed) + int(epoch))
    return rng.permutation(row_count)[:draws_per_epoch]


def _init_progression_head(
    *,
    hidden_dim_2: int,
    seed: int,
) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(seed)
    scale = math.sqrt(2.0 / max(1, hidden_dim_2))
    return {
        "progression_w": rng.normal(
            0.0, scale, (hidden_dim_2, 2)
        ).astype(np.float32),
        "progression_b": np.zeros(2, dtype=np.float32),
    }


def _progression_batch_grads(
    x: np.ndarray,
    labels: np.ndarray,
    params: dict[str, np.ndarray],
    progression: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], float]:
    batch = len(x)
    if batch <= 0:
        raise ValueError("empty progression batch")
    h1 = np.maximum(x @ params["w1"] + params["b1"], 0.0)
    h2 = np.maximum(h1 @ params["w2"] + params["b2"], 0.0)
    prob = elite._softmax(
        h2 @ progression["progression_w"] + progression["progression_b"]
    )
    eps = 1e-7
    loss = -float(
        np.log(np.clip(prob[np.arange(batch), labels], eps, 1.0)).mean()
    )
    dlogits = prob.copy()
    dlogits[np.arange(batch), labels] -= 1.0
    dlogits /= float(batch)

    grad_progression_w = h2.T @ dlogits
    grad_progression_b = dlogits.sum(axis=0)
    dh2 = dlogits @ progression["progression_w"].T
    dpre2 = dh2 * (h2 > 0.0)
    grad_w2 = h1.T @ dpre2
    grad_b2 = dpre2.sum(axis=0)
    dh1 = dpre2 @ params["w2"].T
    dpre1 = dh1 * (h1 > 0.0)
    grad_w1 = x.T @ dpre1
    grad_b1 = dpre1.sum(axis=0)
    return {
        "w1": grad_w1.astype(np.float32),
        "b1": grad_b1.astype(np.float32),
        "w2": grad_w2.astype(np.float32),
        "b2": grad_b2.astype(np.float32),
        "progression_w": grad_progression_w.astype(np.float32),
        "progression_b": grad_progression_b.astype(np.float32),
    }, loss


def _progression_metrics(
    x: np.ndarray,
    labels: np.ndarray,
    params: dict[str, np.ndarray],
    progression: dict[str, np.ndarray],
) -> dict[str, float]:
    h2 = _hidden2(x, params)
    prob = elite._softmax(
        h2 @ progression["progression_w"] + progression["progression_b"]
    )
    prediction = np.argmax(prob, axis=1)
    return {
        "samples": int(len(labels)),
        "accuracy": float((prediction == labels).mean()),
        "auc": _binary_auc(labels, prob[:, 1]),
        "positive_rate": float((labels == 1).mean()),
    }


def _human_floor_issues(
    metrics: dict[str, Any],
    floors: dict[str, Any],
) -> list[str]:
    issues: list[str] = []
    for key in (
        "direction_accuracy",
        "joint_accuracy",
        "kick_f1",
        "future_direction_accuracy",
    ):
        actual = float(metrics.get(key, -math.inf))
        expected = float(floors[key])
        if actual < expected:
            issues.append(f"{key}:{actual:.12f}<{expected:.12f}")
    by_role = metrics.get("by_role") or {}
    for role, expected in floors["by_role_direction_accuracy"].items():
        row = by_role.get(role) or {}
        actual = float(row.get("direction_accuracy", -math.inf))
        if actual < float(expected):
            issues.append(
                f"by_role.{role}.direction_accuracy:{actual:.12f}<{float(expected):.12f}"
            )
    return issues


def _assert_human_epoch_cardinality(
    *,
    epoch: int,
    batches: int,
    sequence_samples: int,
) -> None:
    if int(batches) != EXPECTED_HUMAN_BATCHES_PER_EPOCH:
        raise ValueError(
            "Candidate-I human batch count mismatch in epoch "
            f"{epoch}: {batches} != {EXPECTED_HUMAN_BATCHES_PER_EPOCH}"
        )
    if int(sequence_samples) != EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH:
        raise ValueError(
            "Candidate-I human sequence count mismatch in epoch "
            f"{epoch}: {sequence_samples} != "
            f"{EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH}"
        )


def _runtime_payload(
    *,
    params: dict[str, np.ndarray],
    mean: np.ndarray,
    std: np.ndarray,
    input_columns: list[str],
    window: int,
    future_horizon_steps: int,
    kick_threshold: float,
    kick_thresholds_by_role: dict[int, float],
) -> dict[str, Any]:
    return {
        "schema": "haxlab-elite-js-runtime-v1",
        "source_model_schema": MODEL_SCHEMA,
        "window": int(window),
        "base_input_columns": input_columns,
        "feature_ordering": "team-line-order-v1",
        "role_ids": {name: role_id for role_id, name in ROLE_NAMES.items()},
        "direction_classes": [
            {"class_id": i, "dir_x": dx, "dir_y": dy}
            for i, (dx, dy) in enumerate(elite.ACTION_DIRS)
        ],
        "kick_threshold": float(kick_threshold),
        "kick_thresholds_by_role": {
            ROLE_NAMES[role_id]: float(threshold)
            for role_id, threshold in kick_thresholds_by_role.items()
        },
        "kick_max_distance": elite.RUNTIME_KICK_MAX_DISTANCE,
        "future_horizon_steps": int(future_horizon_steps),
        "future_deadzone": 8.0,
        "mean": mean.astype(float).tolist(),
        "std": std.astype(float).tolist(),
        "weights": {
            key: value.astype(float).tolist()
            for key, value in params.items()
        },
    }


def run_candidate_i(
    *,
    contract_path: Path,
    progression_manifest_path: Path,
    base_model_path: Path,
    base_runtime_path: Path,
    base_metrics_path: Path,
    train_index_path: Path,
    validation_index_path: Path,
    holdout_index_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    contract = _load_contract(contract_path)
    human_cfg = contract["unchanged_human_training"]
    aux_cfg = contract["progression_auxiliary"]
    smoke_cfg = contract["development_smoke"]

    _require_sha256(
        base_model_path,
        contract["base_artifacts"]["model_sha256"],
        "Candidate-E model",
    )
    _require_sha256(
        base_runtime_path,
        contract["base_artifacts"]["runtime_model_sha256"],
        "Candidate-E runtime",
    )
    _require_sha256(
        base_metrics_path,
        contract["base_artifacts"]["metrics_sha256"],
        "Candidate-E metrics",
    )
    _require_sha256(
        train_index_path,
        contract["human_indexes"]["train_sha256"],
        "human train index",
    )
    _require_sha256(
        validation_index_path,
        contract["human_indexes"]["validation_sha256"],
        "human validation index",
    )
    # Hash verification is permitted by the frozen contract; samples stay sealed.
    _require_sha256(
        holdout_index_path,
        contract["human_indexes"]["holdout_sha256"],
        "human holdout index",
    )

    progression_manifest = _load_progression_manifest(
        progression_manifest_path, contract
    )
    train_jsonl = Path(str(progression_manifest["train"]["path"]))
    validation_jsonl = Path(str(progression_manifest["validation"]["path"]))

    train_index = elite._load_index(train_index_path)
    validation_index = elite._load_index(validation_index_path)
    mean, std, input_columns, train_stats = elite._normalization(train_index)
    role_weights = np.asarray(
        train_stats.pop("_role_weights_array"), dtype=np.float32
    )

    base_npz = np.load(base_model_path)
    base = {key: base_npz[key].copy() for key in base_npz.files}
    required = {
        "mean", "std", "role_weights", "w1", "b1", "w2", "b2",
        "wd", "bd", "wk", "bk", "wf", "bf",
    }
    missing = sorted(required - set(base))
    if missing:
        raise ValueError(f"Candidate-E model missing parameters: {missing}")
    if not np.array_equal(mean, base["mean"]) or not np.array_equal(std, base["std"]):
        raise ValueError("Candidate-E normalization differs from frozen human train index")
    if not np.array_equal(role_weights, base["role_weights"]):
        raise ValueError("Candidate-E role weights differ from frozen human train index")

    base_metrics = json.loads(base_metrics_path.read_text(encoding="utf-8"))
    if list(base_metrics.get("base_input_columns") or []) != list(input_columns):
        raise ValueError("Candidate-E input-column ordering mismatch")

    window = int((base_metrics.get("architecture") or {}).get("window") or 8)
    if window != 8:
        raise ValueError(f"Candidate-I requires frozen 8-frame window, got {window}")
    max_gap_ticks = 18
    train_progression_x, train_progression_y, _, _ = _progression_examples(
        train_jsonl,
        index_path=train_index_path,
        mean=mean,
        std=std,
        window=window,
        max_gap_ticks=max_gap_ticks,
    )
    validation_progression_x, validation_progression_y, _, _ = _progression_examples(
        validation_jsonl,
        index_path=validation_index_path,
        mean=mean,
        std=std,
        window=window,
        max_gap_ticks=max_gap_ticks,
    )
    if len(train_progression_y) != int(contract["progression_dataset"]["train_rows"]):
        raise ValueError("Candidate-I train progression reconstruction count mismatch")
    if len(validation_progression_y) != int(
        contract["progression_dataset"]["validation_rows"]
    ):
        raise ValueError("Candidate-I validation progression reconstruction count mismatch")

    probe = _linear_probe(
        _hidden2(train_progression_x, base),
        train_progression_y,
        _hidden2(validation_progression_x, base),
        validation_progression_y,
    )
    if probe["accuracy"] < float(smoke_cfg["frozen_h2_probe_min_accuracy"]):
        raise ValueError(f"Candidate-I frozen-h2 probe accuracy failed: {probe}")
    if probe["auc"] < float(smoke_cfg["frozen_h2_probe_min_auc"]):
        raise ValueError(f"Candidate-I frozen-h2 probe AUC failed: {probe}")

    seed = int(human_cfg["seed"])
    epochs = int(human_cfg["epochs"])
    batch_size = int(human_cfg["batch_size"])
    learning_rate = float(human_cfg["learning_rate"])
    l2 = float(human_cfg["l2"])
    future_loss_weight = float(human_cfg["future_loss_weight"])
    future_horizon_steps = int(human_cfg["future_horizon_steps"])
    state_jitter_std = float(human_cfg["state_jitter"]["std_normalized"])
    hidden_dim = int((base_metrics.get("architecture") or {}).get("hidden_dim") or 128)
    hidden_dim_2 = int(
        (base_metrics.get("architecture") or {}).get("hidden_dim_2")
        or int(aux_cfg["representation_dim"])
    )
    if hidden_dim_2 != int(aux_cfg["representation_dim"]):
        raise ValueError("Candidate-I h2 dimension differs from frozen contract")

    # Human RNG/optimizer exactly follow Candidate E. Auxiliary randomness is isolated.
    human_rng = np.random.default_rng(seed)
    input_dim = len(input_columns) * window + 4
    params = elite._init_params(input_dim, hidden_dim, hidden_dim_2, human_rng)
    human_m = {key: np.zeros_like(value) for key, value in params.items()}
    human_v = {key: np.zeros_like(value) for key, value in params.items()}
    progression = _init_progression_head(hidden_dim_2=hidden_dim_2, seed=seed)
    aux_params = {
        "w1": params["w1"],
        "b1": params["b1"],
        "w2": params["w2"],
        "b2": params["b2"],
        "progression_w": progression["progression_w"],
        "progression_b": progression["progression_b"],
    }
    aux_m = {key: np.zeros_like(value) for key, value in aux_params.items()}
    aux_v = {key: np.zeros_like(value) for key, value in aux_params.items()}

    kick_pos_weight = float(train_stats["kick_positive_weight"])
    human_step = 0
    aux_step = 0
    history: list[dict[str, Any]] = []
    best_score = -math.inf
    best_epoch = 0
    best_params = {key: value.copy() for key, value in params.items()}
    best_progression = {
        key: value.copy() for key, value in progression.items()
    }

    frozen_aux_batch = int(aux_cfg["batch_size"])
    frozen_aux_updates = int(aux_cfg["updates_per_epoch"])
    frozen_aux_draws = int(aux_cfg["draws_per_epoch"])
    if frozen_aux_updates * frozen_aux_batch != frozen_aux_draws:
        raise ValueError("Candidate-I frozen auxiliary cadence is internally inconsistent")
    if int(aux_cfg["epochs"]) != epochs:
        raise ValueError("Candidate-I human/aux epoch count mismatch")

    for epoch in range(1, epochs + 1):
        draw_indices = _progression_draw_indices(
            row_count=len(train_progression_y),
            seed=seed,
            epoch=epoch,
            draws_per_epoch=frozen_aux_draws,
        )
        human_batches = 0
        human_samples = 0
        aux_updates = 0
        human_losses: list[float] = []
        aux_losses: list[float] = []

        iterator = elite._iter_batches(
            train_index,
            mean=mean,
            std=std,
            role_weights=role_weights,
            window=window,
            sequence_stride=1,
            batch_size=batch_size,
            rng=human_rng,
            shuffle=True,
            future_horizon_steps=future_horizon_steps,
            state_jitter_std=state_jitter_std,
            state_jitter_role_ids=(2, 3),
        )
        for x, direction, kick, future_direction, _, sample_weight in iterator:
            grads, losses = elite._train_batch(
                x,
                direction,
                kick,
                future_direction,
                sample_weight,
                params,
                kick_pos_weight=kick_pos_weight,
                future_loss_weight=future_loss_weight,
                l2=l2,
            )
            human_step += 1
            human_batches += 1
            human_samples += len(direction)
            elite._adam_update(
                params,
                grads,
                human_m,
                human_v,
                step=human_step,
                learning_rate=learning_rate,
            )
            human_losses.append(float(losses["loss"]))

            if human_batches % 100 == 0 and aux_updates < frozen_aux_updates:
                start = aux_updates * frozen_aux_batch
                stop = start + frozen_aux_batch
                batch_indices = draw_indices[start:stop]
                if len(batch_indices) != frozen_aux_batch:
                    raise ValueError("Candidate-I auxiliary batch cardinality mismatch")
                aux_grads, aux_loss = _progression_batch_grads(
                    train_progression_x[batch_indices],
                    train_progression_y[batch_indices],
                    params,
                    progression,
                )
                aux_step += 1
                elite._adam_update(
                    aux_params,
                    aux_grads,
                    aux_m,
                    aux_v,
                    step=aux_step,
                    learning_rate=float(aux_cfg["learning_rate"]),
                )
                aux_updates += 1
                aux_losses.append(aux_loss)

        _assert_human_epoch_cardinality(
            epoch=epoch,
            batches=human_batches,
            sequence_samples=human_samples,
        )
        if aux_updates != frozen_aux_updates:
            raise ValueError(
                f"Candidate-I expected {frozen_aux_updates} auxiliary updates "
                f"in epoch {epoch}, got {aux_updates}"
            )

        validation_metrics, _, _ = elite.evaluate(
            validation_index,
            params=params,
            mean=mean,
            std=std,
            role_weights=role_weights,
            window=window,
            sequence_stride=1,
            batch_size=batch_size,
            kick_threshold=0.5,
            future_horizon_steps=future_horizon_steps,
        )
        validation_score = (
            float(validation_metrics["direction_accuracy"])
            + 0.25 * float(validation_metrics["kick_f1"])
            + 0.10 * float(validation_metrics["future_direction_accuracy"])
        )
        progression_validation = _progression_metrics(
            validation_progression_x,
            validation_progression_y,
            params,
            progression,
        )
        if validation_score > best_score:
            best_score = validation_score
            best_epoch = epoch
            best_params = {key: value.copy() for key, value in params.items()}
            best_progression = {
                key: value.copy() for key, value in progression.items()
            }
        history.append(
            {
                "epoch": epoch,
                "human_batches": human_batches,
                "human_sequence_samples": human_samples,
                "human_train_loss_mean": float(np.mean(human_losses)),
                "aux_updates": aux_updates,
                "aux_draws": int(len(draw_indices)),
                "aux_train_loss_mean": float(np.mean(aux_losses)),
                "validation_at_0_5": validation_metrics,
                "validation_selection_score": validation_score,
                "progression_validation": progression_validation,
            }
        )

    params = best_params
    progression = best_progression

    kick_probs, kick_truth, kick_roles = elite._collect_kick_predictions(
        validation_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=1,
        batch_size=batch_size,
        future_horizon_steps=future_horizon_steps,
    )
    best_threshold, kick_calibration, threshold_candidates = (
        elite._calibrate_kick_threshold(
            kick_probs,
            kick_truth,
            max_rate_multiplier=1.5,
        )
    )
    kick_thresholds_by_role: dict[int, float] = {}
    kick_calibration_by_role: dict[str, dict[str, Any]] = {}
    for role_id in range(4):
        mask = kick_roles == role_id
        role_name = ROLE_NAMES[role_id]
        if int(mask.sum()) >= 100 and bool(kick_truth[mask].any()):
            threshold, calibration, candidates = elite._calibrate_kick_threshold(
                kick_probs[mask],
                kick_truth[mask],
                max_rate_multiplier=1.5,
            )
            kick_thresholds_by_role[role_id] = threshold
            kick_calibration_by_role[role_name] = {
                **calibration,
                "samples": int(mask.sum()),
                "source": "role_validation",
                "candidates": candidates,
            }
        else:
            kick_thresholds_by_role[role_id] = best_threshold
            kick_calibration_by_role[role_name] = {
                **kick_calibration,
                "samples": int(mask.sum()),
                "source": "global_fallback",
                "candidates": threshold_candidates,
            }

    final_validation, _, _ = elite.evaluate(
        validation_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=1,
        batch_size=batch_size,
        kick_threshold=best_threshold,
        kick_thresholds_by_role=kick_thresholds_by_role,
        future_horizon_steps=future_horizon_steps,
    )
    final_progression_validation = _progression_metrics(
        validation_progression_x,
        validation_progression_y,
        params,
        progression,
    )
    if final_progression_validation["accuracy"] < float(
        smoke_cfg["post_training_progression_validation_min_accuracy"]
    ) or final_progression_validation["auc"] < float(
        smoke_cfg["post_training_progression_validation_min_auc"]
    ):
        raise ValueError(
            "Candidate-I post-training progression validation gate failed: "
            f"{final_progression_validation}"
        )

    floor_issues = _human_floor_issues(
        final_validation,
        smoke_cfg["human_validation_floors"],
    )
    if floor_issues:
        raise ValueError(
            "Candidate-I human validation regression gate failed: "
            + ", ".join(floor_issues)
        )

    training_metadata = {
        "candidate": CANDIDATE,
        "seed": seed,
        "epochs_requested": epochs,
        "best_epoch": best_epoch,
        "batch_size": batch_size,
        "learning_rate": learning_rate,
        "l2": l2,
        "future_loss_weight": future_loss_weight,
        "future_horizon_steps": future_horizon_steps,
        "state_jitter": human_cfg["state_jitter"],
        "kick_threshold_source": "validation_only",
        "frozen_holdout_used_for_selection": False,
        "auxiliary": {
            "representation": aux_cfg["representation"],
            "batch_size": frozen_aux_batch,
            "updates_per_epoch": frozen_aux_updates,
            "draws_per_epoch": frozen_aux_draws,
            "learning_rate": float(aux_cfg["learning_rate"]),
            "optimizer_state": "separate_adam",
            "sampling": aux_cfg["sampling"],
            "schedule": aux_cfg["schedule"],
            "runtime_serialized": False,
        },
        "train_replays": len(train_index["entries"]),
        "validation_replays": len(validation_index["entries"]),
        "kick_calibration": kick_calibration,
        "kick_calibration_by_role": kick_calibration_by_role,
        "calibrated_kick_threshold": float(best_threshold),
        "calibrated_kick_thresholds_by_role": {
            ROLE_NAMES[role_id]: float(threshold)
            for role_id, threshold in kick_thresholds_by_role.items()
        },
    }
    development_proxy = assert_development_evaluator_gate_compatible(
        training=training_metadata,
        validation_metrics=final_validation,
    )

    # Sealed holdout is opened only after every development gate above passes.
    holdout_index = elite._load_index(holdout_index_path)
    final_holdout, _, _ = elite.evaluate(
        holdout_index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=window,
        sequence_stride=1,
        batch_size=batch_size,
        kick_threshold=best_threshold,
        kick_thresholds_by_role=kick_thresholds_by_role,
        future_horizon_steps=future_horizon_steps,
    )
    if int(final_holdout.get("samples", 0)) <= 0:
        raise ValueError("Candidate-I sealed holdout produced zero samples")

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez_compressed(
        model_path,
        mean=mean,
        std=std,
        role_weights=role_weights,
        **params,
    )
    progression_path = output_dir / "progression-head.npz"
    np.savez_compressed(progression_path, **progression)
    runtime_path = output_dir / "runtime-model.json"
    runtime = _runtime_payload(
        params=params,
        mean=mean,
        std=std,
        input_columns=input_columns,
        window=window,
        future_horizon_steps=future_horizon_steps,
        kick_threshold=best_threshold,
        kick_thresholds_by_role=kick_thresholds_by_role,
    )
    elite._atomic_json(runtime_path, runtime)

    metadata = {
        "schema": MODEL_SCHEMA,
        "candidate": CANDIDATE,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "contract_path": str(contract_path),
        "base_candidate": "E",
        "base_artifacts": {
            "model_sha256": _sha256_file(base_model_path),
            "runtime_model_sha256": _sha256_file(base_runtime_path),
            "metrics_sha256": _sha256_file(base_metrics_path),
        },
        "progression_dataset": {
            "manifest_path": str(progression_manifest_path),
            "train_rows": len(train_progression_y),
            "validation_rows": len(validation_progression_y),
            "holdout_materialized": False,
        },
        "frozen_h2_probe": probe,
        "training": training_metadata,
        "history": history,
        "final_progression_validation": final_progression_validation,
        "development_evidence_preflight": {
            "passed": True,
            "schema": development_proxy["schema"],
            "schema_preflight_only": True,
        },
        "final_validation": final_validation,
        "final_holdout": final_holdout,
        "sealed_holdout_evaluations": 1,
        "promotion_v5_evaluated": False,
        "production_pointer_writes": False,
        "model_path": str(model_path),
        "runtime_model_path": str(runtime_path),
        "progression_head_path": str(progression_path),
        "progression_head_in_runtime": False,
    }
    elite._atomic_json(output_dir / "metrics.json", metadata)
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-train-candidate-i")
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--progression-manifest", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--base-runtime", type=Path, required=True)
    parser.add_argument("--base-metrics", type=Path, required=True)
    parser.add_argument("--train-index", type=Path, required=True)
    parser.add_argument("--validation-index", type=Path, required=True)
    parser.add_argument("--holdout-index", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metadata = run_candidate_i(
        contract_path=args.contract,
        progression_manifest_path=args.progression_manifest,
        base_model_path=args.base_model,
        base_runtime_path=args.base_runtime,
        base_metrics_path=args.base_metrics,
        train_index_path=args.train_index,
        validation_index_path=args.validation_index,
        holdout_index_path=args.holdout_index,
        output_dir=args.output_dir,
    )
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
