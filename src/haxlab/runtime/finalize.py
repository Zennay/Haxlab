from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.learning.selector import MANIFEST_SCHEMA, build_training_manifest
from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState
from haxlab.skill.leaderboard import build_leaderboard


LEADERBOARD_SCHEMA = "haxlab-skill-leaderboard-v1"


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


def _require_non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a native non-negative integer")
    return value


def _require_positive_int(value: object, field: str) -> int:
    validated = _require_non_negative_int(value, field)
    if validated == 0:
        raise ValueError(f"{field} must be a native positive integer")
    return validated


def _require_non_negative_finite_number(value: object, field: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{field} must be a native finite non-negative number")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0.0:
        raise ValueError(f"{field} must be a native finite non-negative number")
    return normalized


def _completion_matches_snapshot(
    previous: object,
    *,
    raw_unique: int,
    analysis_ok: int,
    current_ticks: int,
) -> bool:
    if type(previous) is not dict:
        return False
    return (
        previous.get("analysis_version") == CURRENT_ANALYZER_VERSION
        and type(previous.get("raw_unique_replays")) is int
        and previous["raw_unique_replays"] == raw_unique
        and type(previous.get("analysis_ok")) is int
        and previous["analysis_ok"] == analysis_ok
        and type(previous.get("analysis_ticks_reconstructed")) is int
        and previous["analysis_ticks_reconstructed"] == current_ticks
    )


def _leaderboard_matches_contract(
    previous: object,
    *,
    analysis_root: Path,
    min_matches: int,
    min_minutes: float,
) -> bool:
    if type(previous) is not dict:
        return False
    if (
        previous.get("schema") != LEADERBOARD_SCHEMA
        or previous.get("analysis_version") != CURRENT_ANALYZER_VERSION
        or previous.get("source_root") != str(analysis_root)
        or type(previous.get("min_matches")) is not int
        or previous["min_matches"] != min_matches
    ):
        return False

    previous_min_minutes = previous.get("min_minutes")
    if type(previous_min_minutes) not in {int, float}:
        return False
    normalized_minutes = float(previous_min_minutes)
    if (
        not math.isfinite(normalized_minutes)
        or normalized_minutes < 0.0
        or normalized_minutes != min_minutes
    ):
        return False

    rows = previous.get("rows")
    if type(rows) is not list:
        return False
    for row in rows:
        if type(row) is not dict:
            return False
        matches = row.get("matches")
        minutes = row.get("minutes")
        if type(matches) is not int or matches < 0:
            return False
        if type(minutes) not in {int, float}:
            return False
        normalized_row_minutes = float(minutes)
        if (
            not math.isfinite(normalized_row_minutes)
            or normalized_row_minutes < 0.0
        ):
            return False
    return True


def _training_manifest_counts(
    manifest: object,
) -> tuple[int, int, int]:
    if type(manifest) is not dict or manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError("training manifest must use the current manifest schema")
    stats = manifest.get("stats")
    if type(stats) is not dict:
        raise ValueError("training manifest stats must be an object")
    selected_players = _require_non_negative_int(
        stats.get("selected_player_count"),
        "training manifest stats.selected_player_count",
    )
    training_replays = _require_non_negative_int(
        stats.get("train_replay_count"),
        "training manifest stats.train_replay_count",
    )
    holdout_replays = _require_non_negative_int(
        stats.get("holdout_replay_count"),
        "training manifest stats.holdout_replay_count",
    )
    return selected_players, training_replays, holdout_replays


def finalize_analysis_if_ready(
    state: RuntimeState,
    *,
    derived_root: Path,
    min_matches: int = 20,
    min_minutes: float = 60.0,
) -> dict[str, Any]:
    validated_min_matches = _require_positive_int(min_matches, "min_matches")
    validated_min_minutes = _require_non_negative_finite_number(
        min_minutes,
        "min_minutes",
    )

    snapshot = state.status_snapshot()
    raw_unique = _require_non_negative_int(
        snapshot.get("raw_unique_replays"),
        "snapshot.raw_unique_replays",
    )
    analysis_ok = _require_non_negative_int(
        snapshot.get("analysis_ok"),
        "snapshot.analysis_ok",
    )
    analysis_failed = _require_non_negative_int(
        snapshot.get("analysis_failed"),
        "snapshot.analysis_failed",
    )
    analysis_pending = _require_non_negative_int(
        snapshot.get("analysis_pending"),
        "snapshot.analysis_pending",
    )

    if (
        raw_unique <= 0
        or analysis_ok != raw_unique
        or analysis_failed != 0
        or analysis_pending != 0
    ):
        return {
            "status": "not_ready",
            "analysis_version": CURRENT_ANALYZER_VERSION,
            "analysis_ok": analysis_ok,
            "analysis_failed": analysis_failed,
            "analysis_pending": analysis_pending,
            "raw_unique_replays": raw_unique,
        }

    current_ticks = _require_non_negative_int(
        snapshot.get("analysis_ticks_reconstructed"),
        "snapshot.analysis_ticks_reconstructed",
    )
    current_samples = _require_non_negative_int(
        snapshot.get("analysis_sampled_states"),
        "snapshot.analysis_sampled_states",
    )
    current_events = _require_non_negative_int(
        snapshot.get("analysis_raw_events"),
        "snapshot.analysis_raw_events",
    )

    analysis_root = derived_root / CURRENT_ANALYZER_VERSION
    leaderboard_path = (
        derived_root / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    )
    completion_path = analysis_root / "_complete.json"
    training_manifest_path = (
        derived_root
        / "training"
        / f"human-imitation-{CURRENT_ANALYZER_VERSION}.json"
    )

    if (
        leaderboard_path.exists()
        and completion_path.exists()
        and training_manifest_path.exists()
    ):
        try:
            previous: object = json.loads(
                completion_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            previous = {}
        try:
            previous_leaderboard: object = json.loads(
                leaderboard_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            previous_leaderboard = {}
        try:
            previous_manifest: object = json.loads(
                training_manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError):
            previous_manifest = {}

        if (
            _completion_matches_snapshot(
                previous,
                raw_unique=raw_unique,
                analysis_ok=analysis_ok,
                current_ticks=current_ticks,
            )
            and _leaderboard_matches_contract(
                previous_leaderboard,
                analysis_root=analysis_root,
                min_matches=validated_min_matches,
                min_minutes=validated_min_minutes,
            )
            and type(previous_manifest) is dict
            and previous_manifest.get("schema") == MANIFEST_SCHEMA
        ):
            return {
                "status": "already_finalized",
                "analysis_version": CURRENT_ANALYZER_VERSION,
                "leaderboard_path": str(leaderboard_path),
                "completion_path": str(completion_path),
                "training_manifest_path": str(training_manifest_path),
            }

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(build_leaderboard(analysis_root)):
        if type(row) is not dict:
            raise ValueError(f"leaderboard row {index} must be an object")
        matches = _require_non_negative_int(
            row.get("matches"),
            f"leaderboard row {index}.matches",
        )
        minutes = _require_non_negative_finite_number(
            row.get("minutes"),
            f"leaderboard row {index}.minutes",
        )
        if (
            matches >= validated_min_matches
            and minutes >= validated_min_minutes
        ):
            rows.append(row)

    generated_at = datetime.now(timezone.utc).isoformat()
    leaderboard = {
        "schema": LEADERBOARD_SCHEMA,
        "analysis_version": CURRENT_ANALYZER_VERSION,
        "generated_at": generated_at,
        "source_root": str(analysis_root),
        "min_matches": validated_min_matches,
        "min_minutes": validated_min_minutes,
        "rows": rows,
    }
    _atomic_json(leaderboard_path, leaderboard)

    training_manifest = build_training_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=derived_root.parent / "raw" / "replays",
    )
    (
        training_selected_players,
        training_replays,
        holdout_replays,
    ) = _training_manifest_counts(training_manifest)
    _atomic_json(training_manifest_path, training_manifest)

    completion = {
        "schema": "haxlab-analysis-completion-v1",
        "analysis_version": CURRENT_ANALYZER_VERSION,
        "completed_at": generated_at,
        "raw_unique_replays": raw_unique,
        "analysis_ok": analysis_ok,
        "analysis_failed": analysis_failed,
        "analysis_pending": analysis_pending,
        "analysis_ticks_reconstructed": current_ticks,
        "analysis_sampled_states": current_samples,
        "analysis_raw_events": current_events,
        "leaderboard_players": len(rows),
        "leaderboard_path": str(leaderboard_path),
        "training_manifest_path": str(training_manifest_path),
        "training_selected_players": training_selected_players,
        "training_replays": training_replays,
        "holdout_replays": holdout_replays,
        "top_preview": [
            {
                "name": row.get("name"),
                "role": row.get("role"),
                "rating": row.get("rating"),
                "rating_uncertainty": row.get("rating_uncertainty"),
                "matches": row.get("matches"),
                "minutes": row.get("minutes"),
            }
            for row in rows[:10]
        ],
    }
    _atomic_json(completion_path, completion)

    state.event(
        "analysis_finalized",
        subject=CURRENT_ANALYZER_VERSION,
        detail=(
            f"replays={analysis_ok};"
            f"players={len(rows)};"
            f"leaderboard={leaderboard_path}"
        ),
    )

    return {
        "status": "finalized",
        "analysis_version": CURRENT_ANALYZER_VERSION,
        "leaderboard_path": str(leaderboard_path),
        "completion_path": str(completion_path),
        "training_manifest_path": str(training_manifest_path),
        "leaderboard_players": len(rows),
        "training_replays": training_replays,
        "holdout_replays": holdout_replays,
    }
