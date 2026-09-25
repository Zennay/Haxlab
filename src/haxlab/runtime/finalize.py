from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState
from haxlab.skill.leaderboard import build_leaderboard


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


def finalize_analysis_if_ready(
    state: RuntimeState,
    *,
    derived_root: Path,
    min_matches: int = 20,
    min_minutes: float = 60.0,
) -> dict[str, Any]:
    snapshot = state.status_snapshot()
    raw_unique = int(snapshot.get("raw_unique_replays", 0))
    analysis_ok = int(snapshot.get("analysis_ok", 0))
    analysis_failed = int(snapshot.get("analysis_failed", 0))
    analysis_pending = int(snapshot.get("analysis_pending", 0))

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

    analysis_root = derived_root / CURRENT_ANALYZER_VERSION
    leaderboard_path = (
        derived_root / "leaderboards" / f"{CURRENT_ANALYZER_VERSION}.json"
    )
    completion_path = analysis_root / "_complete.json"

    if leaderboard_path.exists() and completion_path.exists():
        try:
            previous = json.loads(completion_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}

        current_ticks = int(snapshot.get("analysis_ticks_reconstructed", 0))
        if (
            previous.get("analysis_version") == CURRENT_ANALYZER_VERSION
            and int(previous.get("raw_unique_replays", -1)) == raw_unique
            and int(previous.get("analysis_ok", -1)) == analysis_ok
            and int(previous.get("analysis_ticks_reconstructed", -1))
            == current_ticks
        ):
            return {
                "status": "already_finalized",
                "analysis_version": CURRENT_ANALYZER_VERSION,
                "leaderboard_path": str(leaderboard_path),
                "completion_path": str(completion_path),
            }

    rows = [
        row
        for row in build_leaderboard(analysis_root)
        if int(row.get("matches", 0)) >= max(1, min_matches)
        and float(row.get("minutes", 0.0)) >= max(0.0, min_minutes)
    ]

    generated_at = datetime.now(timezone.utc).isoformat()
    leaderboard = {
        "schema": "haxlab-skill-leaderboard-v1",
        "analysis_version": CURRENT_ANALYZER_VERSION,
        "generated_at": generated_at,
        "source_root": str(analysis_root),
        "min_matches": max(1, min_matches),
        "min_minutes": max(0.0, min_minutes),
        "rows": rows,
    }
    _atomic_json(leaderboard_path, leaderboard)

    completion = {
        "schema": "haxlab-analysis-completion-v1",
        "analysis_version": CURRENT_ANALYZER_VERSION,
        "completed_at": generated_at,
        "raw_unique_replays": raw_unique,
        "analysis_ok": analysis_ok,
        "analysis_failed": analysis_failed,
        "analysis_pending": analysis_pending,
        "analysis_ticks_reconstructed": int(
            snapshot.get("analysis_ticks_reconstructed", 0)
        ),
        "analysis_sampled_states": int(
            snapshot.get("analysis_sampled_states", 0)
        ),
        "analysis_raw_events": int(snapshot.get("analysis_raw_events", 0)),
        "leaderboard_players": len(rows),
        "leaderboard_path": str(leaderboard_path),
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
        "leaderboard_players": len(rows),
    }
