from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from haxlab.analysis.roles import ROLES_4V4, infer_roles_4v4


def _healthy_candidate(
    *,
    sha256: str,
    raw_path: str,
    analysis_path: str,
    sampled_states: int,
) -> dict[str, Any] | None:
    raw = Path(raw_path)
    analysis_file = Path(analysis_path)
    if not raw.exists() or not analysis_file.exists():
        return None
    try:
        payload = json.loads(analysis_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if int(payload.get("schemaVersion") or 0) < 4:
        return None
    total_frames = int(payload.get("totalFrames") or 0)
    if total_frames < 60 * 180:
        return None
    touches = int((payload.get("featureSummary") or {}).get("touches") or 0)
    if touches < 100:
        return None

    players = list(payload.get("players") or [])
    simulation = payload.get("simulation") or {}
    sampled_state_count = int(
        simulation.get("sampledStateCount") or sampled_states or 0
    )
    if sampled_state_count <= 0:
        return None

    average_team_sizes: dict[int, float] = {}
    for team_id in (1, 2):
        team_sample_total = sum(
            int(player.get("samples") or 0)
            for player in players
            if int(player.get("teamId") or 0) == team_id
        )
        average_team_size = team_sample_total / sampled_state_count
        average_team_sizes[team_id] = average_team_size
        if not 3.5 <= average_team_size <= 4.5:
            return None

    roles = infer_roles_4v4(players)
    core_roles: dict[int, dict[str, int]] = {1: {}, 2: {}}
    role_confidences: list[float] = []

    for team_id in (1, 2):
        team_players = [
            player
            for player in players
            if int(player.get("teamId") or 0) == team_id
            and int(player.get("samples") or 0) > 0
        ]
        core = sorted(
            team_players,
            key=lambda row: int(row.get("samples") or 0),
            reverse=True,
        )[:4]
        if len(core) != 4:
            return None

        seen: set[str] = set()
        for player in core:
            player_id = int(player["id"])
            role_row = roles.get(player_id) or {}
            role = str(role_row.get("role") or "unknown")
            confidence = float(role_row.get("confidence") or 0.0)
            if role not in ROLES_4V4 or confidence < 0.55 or role in seen:
                return None
            seen.add(role)
            core_roles[team_id][role] = player_id
            role_confidences.append(confidence)
        if seen != set(ROLES_4V4):
            return None

    return {
        "schema": "haxlab-replay-scenario-source-v1",
        "sha256": sha256,
        "raw_path": str(raw),
        "analysis_path": str(analysis_file),
        "duration_seconds": total_frames / 60.0,
        "sampled_states": int(sampled_states),
        "average_team_sizes": {
            str(team_id): round(value, 4)
            for team_id, value in average_team_sizes.items()
        },
        "touches": touches,
        "role_confidence_min": min(role_confidences),
        "roles": {
            str(team_id): mapping for team_id, mapping in core_roles.items()
        },
    }


def select_scenario_source(
    db_path: Path,
    *,
    max_candidates: int = 1000,
) -> dict[str, Any]:
    db = sqlite3.connect(db_path)
    try:
        rows = db.execute(
            """
            SELECT
                a.sha256,
                r.archive_path,
                a.output_path,
                COALESCE(a.sampled_state_count, 0)
            FROM replay_analysis_versions a
            JOIN raw_replays r ON r.sha256 = a.sha256
            WHERE a.analyzer_version = 'state-pass-v4'
              AND a.status = 'ok'
              AND a.output_path IS NOT NULL
              AND COALESCE(a.player_count, 0) >= 8
            ORDER BY
                COALESCE(a.sampled_state_count, 0) DESC,
                a.sha256 ASC
            LIMIT ?
            """,
            (max(1, max_candidates),),
        ).fetchall()
    finally:
        db.close()

    for sha256, raw_path, analysis_path, sampled_states in rows:
        candidate = _healthy_candidate(
            sha256=str(sha256),
            raw_path=str(raw_path),
            analysis_path=str(analysis_path),
            sampled_states=int(sampled_states or 0),
        )
        if candidate is not None:
            return candidate

    raise RuntimeError(
        "No healthy role-resolved 4v4 replay found among "
        f"{min(len(rows), max_candidates)} candidates"
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-scenario-source")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument("--max-candidates", type=int, default=1000)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = select_scenario_source(
        args.state_db,
        max_candidates=max(1, args.max_candidates),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
