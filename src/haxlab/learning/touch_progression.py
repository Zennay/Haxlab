from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np


DATASET_SCHEMA = "haxlab-candidate-i-touch-progression-dataset-v1"
OUTPUT_SCHEMA = "haxlab-candidate-i-touch-progression-materialized-v1"
ROLE_NAMES = {0: "gk", 1: "dm", 2: "am", 3: "st"}
OUTCOME_NAMES = {1: "self_retouch", 2: "teammate", 3: "opponent", 4: "goal"}


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(path: Path, expected: str, label: str) -> str:
    actual = _sha256_path(path)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return actual


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _load_index(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = list(payload.get("entries") or [])
    if not entries:
        raise ValueError(f"{path}: index has no entries")
    return {**payload, "entries": entries}


def _replay_sha(entry: dict[str, Any]) -> str:
    shard_path = Path(str(entry["shard_path"]))
    name = shard_path.name
    if not name.endswith(".f32.gz"):
        raise ValueError(f"{shard_path}: unsupported elite shard suffix")
    sha = name.removesuffix(".f32.gz")
    if len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha.lower()):
        raise ValueError(f"{shard_path}: invalid replay SHA-256 basename")
    return sha.lower()


def _replay_shas(index: dict[str, Any]) -> set[str]:
    shas = [_replay_sha(entry) for entry in index["entries"]]
    if len(shas) != len(set(shas)):
        raise ValueError("elite index contains duplicate replay SHA-256 entries")
    return set(shas)


def _load_shard(entry: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    shard_path = Path(str(entry["shard_path"]))
    sha = _replay_sha(entry)
    meta_path = shard_path.with_name(f"{sha}.meta.json")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    columns = list(meta.get("columns") or [])
    row_width = int(meta.get("rowWidth") or len(columns))
    if row_width <= 0 or row_width != len(columns):
        raise ValueError(f"{meta_path}: invalid row width")
    with gzip.open(shard_path, "rb") as handle:
        raw = handle.read()
    values = np.frombuffer(raw, dtype="<f4")
    if values.size % row_width != 0:
        raise ValueError(
            f"{shard_path}: invalid float32 count {values.size} for row width {row_width}"
        )
    return values.reshape(-1, row_width), meta


def _state_path(state_root: Path, replay_sha: str) -> Path:
    return state_root / replay_sha[:2] / replay_sha[2:4] / f"{replay_sha}.json"


def _selected_replay_to_player_index(meta: dict[str, Any]) -> dict[int, int]:
    selected_players = meta.get("selectedPlayers") or {}
    identity_to_index = {
        str(identity): int(index) for index, identity in selected_players.items()
    }
    result: dict[int, int] = {}
    for replay_player_id, spec in (meta.get("selectedReplayPlayers") or {}).items():
        identity = str((spec or {}).get("identity") or "")
        if identity not in identity_to_index:
            raise ValueError(
                f"selected replay player {replay_player_id} references "
                f"unknown identity {identity!r}"
            )
        result[int(replay_player_id)] = identity_to_index[identity]
    return result


def _row_key(
    split: str,
    replay_sha: str,
    replay_player_id: int,
    touch_frame: int,
    sample_frame: int,
    role_id: int,
    target: int,
    progression: float,
) -> str:
    return (
        f"{split}|{replay_sha}|{replay_player_id}|{touch_frame}|"
        f"{sample_frame}|{role_id}|{target}|{progression:.9f}"
    )


def _collect_split(
    *,
    split: str,
    index: dict[str, Any],
    state_root: Path,
    rules: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    window = int(rules["temporal_window_frames"])
    pre_touch_min = int(rules["pre_touch_min_ticks"])
    pre_touch_max = int(rules["pre_touch_max_ticks"])
    max_contiguous_gap = int(rules["max_contiguous_sample_gap_ticks"])
    allowed_outcomes = {int(value) for value in rules["allowed_touch_outcome_codes"]}
    if window <= 1:
        raise ValueError("temporal_window_frames must be > 1")
    if pre_touch_min <= 0 or pre_touch_max < pre_touch_min:
        raise ValueError("invalid pre-touch tick range")
    if max_contiguous_gap <= 0:
        raise ValueError("max_contiguous_sample_gap_ticks must be positive")

    rows_out: list[dict[str, Any]] = []
    digest = hashlib.sha256()
    role_counts = {name: 0 for name in ROLE_NAMES.values()}
    outcome_counts = {name: 0 for name in OUTCOME_NAMES.values()}
    positive = 0
    negative = 0
    missing_state = 0
    dropped_gap = 0
    dropped_window = 0

    for entry in index["entries"]:
        replay_sha = _replay_sha(entry)
        state_path = _state_path(state_root, replay_sha)
        if not state_path.exists():
            missing_state += 1
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        touches = ((state.get("sparseEvents") or {}).get("touches") or [])
        shard_rows, meta = _load_shard(entry)
        replay_to_player_index = _selected_replay_to_player_index(meta)
        if not replay_to_player_index:
            continue

        columns = list(meta["columns"])
        by_name = {name: index for index, name in enumerate(columns)}
        required_columns = {"frame", "player_index", "role_id"}
        missing_columns = sorted(required_columns - set(by_name))
        if missing_columns:
            raise ValueError(f"{entry['shard_path']}: missing columns {missing_columns}")

        frames = shard_rows[:, by_name["frame"]].astype(np.int64)
        player_indices = shard_rows[:, by_name["player_index"]].astype(np.int64)
        role_ids = shard_rows[:, by_name["role_id"]].astype(np.int64)

        per_player: dict[int, np.ndarray] = {}
        for player_index in np.unique(player_indices):
            ids = np.flatnonzero(player_indices == player_index)
            per_player[int(player_index)] = ids[
                np.argsort(frames[ids], kind="stable")
            ]

        for touch in touches:
            if not isinstance(touch, list) or len(touch) < 10:
                continue
            replay_player_id = int(touch[1])
            if replay_player_id not in replay_to_player_index:
                continue
            outcome = int(touch[7])
            progression_value = touch[9]
            if outcome not in allowed_outcomes or progression_value is None:
                continue

            player_index = replay_to_player_index[replay_player_id]
            ids = per_player.get(player_index)
            if ids is None or len(ids) < window:
                dropped_window += 1
                continue

            touch_frame = int(touch[0])
            player_frames = frames[ids]
            target_frame = touch_frame - pre_touch_min
            position = int(np.searchsorted(player_frames, target_frame, side="right") - 1)
            if position < window - 1:
                dropped_window += 1
                continue

            sample_frame = int(player_frames[position])
            lag = touch_frame - sample_frame
            if lag < pre_touch_min or lag > pre_touch_max:
                dropped_gap += 1
                continue

            window_ids = ids[position - window + 1 : position + 1]
            if np.any(np.diff(frames[window_ids]) > max_contiguous_gap):
                dropped_window += 1
                continue

            role_id = int(role_ids[ids[position]])
            if role_id not in ROLE_NAMES:
                dropped_window += 1
                continue

            progression = float(progression_value)
            target = 1 if progression > 0.0 else 0
            key = _row_key(
                split,
                replay_sha,
                replay_player_id,
                touch_frame,
                sample_frame,
                role_id,
                target,
                progression,
            )
            digest.update(key.encode("utf-8"))
            digest.update(b"\n")
            fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
            rows_out.append(
                {
                    "schema": "haxlab-candidate-i-touch-progression-row-v1",
                    "split": split,
                    "source_replay_sha256": replay_sha,
                    "replay_player_id": replay_player_id,
                    "player_index": player_index,
                    "role_id": role_id,
                    "role": ROLE_NAMES[role_id],
                    "touch_frame": touch_frame,
                    "sample_frame": sample_frame,
                    "pre_touch_lag_ticks": lag,
                    "outcome": outcome,
                    "progression": progression,
                    "positive_progression": target,
                    "row_fingerprint": fingerprint,
                }
            )
            if target:
                positive += 1
            else:
                negative += 1
            role_counts[ROLE_NAMES[role_id]] += 1
            outcome_counts[OUTCOME_NAMES[outcome]] += 1

    stats = {
        "split": split,
        "replays": len(index["entries"]),
        "replays_with_state": len(index["entries"]) - missing_state,
        "missing_state_replays": missing_state,
        "rows": len(rows_out),
        "positive": positive,
        "negative": negative,
        "positive_rate": positive / max(1, len(rows_out)),
        "by_role": role_counts,
        "touch_outcomes": outcome_counts,
        "dropped_gap": dropped_gap,
        "dropped_window": dropped_window,
        "row_fingerprint_digest_sha256": digest.hexdigest(),
    }
    return rows_out, stats


def _verify_expected(actual: dict[str, Any], expected: dict[str, Any], split: str) -> None:
    exact_keys = (
        "replays",
        "rows",
        "positive",
        "negative",
        "missing_state_replays",
        "row_fingerprint_digest_sha256",
    )
    for key in exact_keys:
        if actual.get(key) != expected.get(key):
            raise ValueError(
                f"{split} {key} mismatch: expected {expected.get(key)!r}, "
                f"got {actual.get(key)!r}"
            )
    for key in ("by_role", "touch_outcomes"):
        if actual.get(key) != expected.get(key):
            raise ValueError(f"{split} {key} mismatch")
    expected_rate = float(expected["positive_rate"])
    if abs(float(actual["positive_rate"]) - expected_rate) > 1e-12:
        raise ValueError(
            f"{split} positive_rate mismatch: expected {expected_rate}, "
            f"got {actual['positive_rate']}"
        )


def _jsonl_text(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )


def build_touch_progression_dataset(
    *,
    descriptor_path: Path,
    train_index_path: Path,
    validation_index_path: Path,
    holdout_index_path: Path,
    state_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    if descriptor.get("schema") != DATASET_SCHEMA:
        raise ValueError("unsupported Candidate-I touch-progression descriptor schema")
    if not descriptor.get("frozen_before_candidate_i_training"):
        raise ValueError("Candidate-I progression descriptor is not frozen")

    source_hashes = descriptor["source_index_sha256"]
    _require_sha256(train_index_path, str(source_hashes["train"]), "train index")
    _require_sha256(
        validation_index_path,
        str(source_hashes["validation"]),
        "validation index",
    )
    _require_sha256(
        holdout_index_path,
        str(source_hashes["holdout_exclusion"]),
        "holdout index",
    )

    train_index = _load_index(train_index_path)
    validation_index = _load_index(validation_index_path)
    holdout_index = _load_index(holdout_index_path)

    train_shas = _replay_shas(train_index)
    validation_shas = _replay_shas(validation_index)
    holdout_shas = _replay_shas(holdout_index)
    overlaps = {
        "train_validation": len(train_shas & validation_shas),
        "train_holdout": len(train_shas & holdout_shas),
        "validation_holdout": len(validation_shas & holdout_shas),
    }
    if overlaps != descriptor["split_overlap_replays"]:
        raise ValueError(
            f"Candidate-I replay overlap mismatch: expected "
            f"{descriptor['split_overlap_replays']}, got {overlaps}"
        )
    if any(overlaps.values()):
        raise ValueError(f"Candidate-I replay split leakage detected: {overlaps}")

    train_rows, train_stats = _collect_split(
        split="train",
        index=train_index,
        state_root=state_root,
        rules=descriptor["rules"],
    )
    validation_rows, validation_stats = _collect_split(
        split="validation",
        index=validation_index,
        state_root=state_root,
        rules=descriptor["rules"],
    )
    _verify_expected(train_stats, descriptor["train"], "train")
    _verify_expected(validation_stats, descriptor["validation"], "validation")

    train_text = _jsonl_text(train_rows)
    validation_text = _jsonl_text(validation_rows)
    train_output = output_dir / "train.jsonl"
    validation_output = output_dir / "validation.jsonl"
    _atomic_text(train_output, train_text)
    _atomic_text(validation_output, validation_text)

    manifest = {
        "schema": OUTPUT_SCHEMA,
        "descriptor_path": str(descriptor_path),
        "descriptor_sha256": _sha256_path(descriptor_path),
        "state_root": str(state_root),
        "rules": descriptor["rules"],
        "split_overlap_replays": overlaps,
        "train": {
            **train_stats,
            "path": str(train_output),
            "file_sha256": hashlib.sha256(train_text.encode("utf-8")).hexdigest(),
        },
        "validation": {
            **validation_stats,
            "path": str(validation_output),
            "file_sha256": hashlib.sha256(
                validation_text.encode("utf-8")
            ).hexdigest(),
        },
        "holdout_materialized": False,
    }
    _atomic_text(
        output_dir / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-build-touch-progression")
    parser.add_argument("--descriptor", type=Path, required=True)
    parser.add_argument("--train-index", type=Path, required=True)
    parser.add_argument("--validation-index", type=Path, required=True)
    parser.add_argument("--holdout-index", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_touch_progression_dataset(
        descriptor_path=args.descriptor,
        train_index_path=args.train_index,
        validation_index_path=args.validation_index,
        holdout_index_path=args.holdout_index,
        state_root=args.state_root,
        output_dir=args.output_dir,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
