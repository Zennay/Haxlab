from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from haxlab.analysis.roles import ROLES_4V4, infer_roles_4v4
from haxlab.hashing import sha256_file


def _native_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _positive_native_int(value: Any, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a native positive integer")
    return value


def _normalized_exclusions(values: set[str] | None) -> set[str]:
    normalized: set[str] = set()
    for value in values or set():
        if not isinstance(value, str):
            raise ValueError("exclude_sha256 values must be strings")
        digest = value.strip().lower()
        if len(digest) != 64 or any(
            ch not in "0123456789abcdef" for ch in digest
        ):
            raise ValueError(
                "exclude_sha256 values must be 64-character hexadecimal SHA-256"
            )
        normalized.add(digest)
    return normalized


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _healthy_candidate(
    *,
    sha256: str,
    raw_path: str,
    analysis_path: str,
    sampled_states: int,
) -> dict[str, Any] | None:
    if not isinstance(sha256, str):
        return None
    if not isinstance(raw_path, str) or not isinstance(analysis_path, str):
        return None
    raw = Path(raw_path)
    analysis_file = Path(analysis_path)
    if (
        raw.is_symlink()
        or analysis_file.is_symlink()
        or not raw.is_file()
        or not analysis_file.is_file()
    ):
        return None
    try:
        analysis_bytes = analysis_file.read_bytes()
        payload = json.loads(analysis_bytes)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    analysis_sha256 = hashlib.sha256(analysis_bytes).hexdigest()
    if not isinstance(payload, dict):
        return None

    schema_version = _native_int(payload.get("schemaVersion"))
    total_frames = _native_int(payload.get("totalFrames"))
    if schema_version is None or schema_version < 4:
        return None
    if total_frames is None or total_frames < 60 * 180:
        return None

    feature_summary = payload.get("featureSummary") or {}
    if not isinstance(feature_summary, dict):
        return None
    raw_touches = feature_summary.get("touches")
    touches = _native_int(0 if raw_touches is None else raw_touches)
    if touches is None or touches < 100:
        return None

    raw_players = payload.get("players") or []
    if not isinstance(raw_players, list):
        return None
    players: list[dict[str, Any]] = []
    player_ids: set[int] = set()
    for player in raw_players:
        if not isinstance(player, dict):
            return None
        player_id = _native_int(player.get("id"))
        raw_team_id = player.get("teamId")
        raw_samples = player.get("samples")
        team_id = _native_int(0 if raw_team_id is None else raw_team_id)
        samples = _native_int(0 if raw_samples is None else raw_samples)
        if (
            player_id is None
            or team_id is None
            or samples is None
            or samples < 0
            or player_id in player_ids
        ):
            return None
        player_ids.add(player_id)
        players.append(player)

    simulation = payload.get("simulation") or {}
    if not isinstance(simulation, dict):
        return None
    expected_sampled_states = _native_int(sampled_states)
    if expected_sampled_states is None or expected_sampled_states <= 0:
        return None
    declared_sampled_states = simulation.get("sampledStateCount")
    if declared_sampled_states is None:
        sampled_state_count = expected_sampled_states
    else:
        sampled_state_count = _native_int(declared_sampled_states)
        if sampled_state_count is None:
            return None
        if sampled_state_count == 0:
            sampled_state_count = expected_sampled_states
    if sampled_state_count <= 0:
        return None
    if expected_sampled_states != sampled_state_count:
        return None

    average_team_sizes: dict[int, float] = {}
    for team_id in (1, 2):
        team_sample_total = sum(
            int(player["samples"])
            for player in players
            if int(player.get("teamId") or 0) == team_id
        )
        average_team_size = team_sample_total / sampled_state_count
        average_team_sizes[team_id] = average_team_size
        if not 3.5 <= average_team_size <= 4.5:
            return None

    try:
        roles = infer_roles_4v4(players)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not isinstance(roles, dict):
        return None

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
            if not isinstance(role_row, dict):
                return None
            role = role_row.get("role")
            confidence = _finite_number(role_row.get("confidence"))
            if (
                not isinstance(role, str)
                or confidence is None
                or role not in ROLES_4V4
                or not 0.55 <= confidence <= 1.0
                or role in seen
            ):
                return None
            seen.add(role)
            core_roles[team_id][role] = player_id
            role_confidences.append(confidence)
        if seen != set(ROLES_4V4):
            return None

    normalized_sha = str(sha256).strip().lower()
    if (
        len(normalized_sha) != 64
        or any(ch not in "0123456789abcdef" for ch in normalized_sha)
    ):
        return None
    try:
        actual_raw_sha = sha256_file(raw)
    except OSError:
        return None
    if actual_raw_sha != normalized_sha:
        return None

    return {
        "schema": "haxlab-replay-scenario-source-v1",
        "sha256": normalized_sha,
        "raw_path": str(raw),
        "analysis_path": str(analysis_file),
        "analysis_sha256": analysis_sha256,
        "duration_seconds": total_frames / 60.0,
        "sampled_states": sampled_state_count,
        "raw_file_sha256_verified": True,
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
    exclude_sha256: set[str] | None = None,
) -> dict[str, Any]:
    candidate_limit = _positive_native_int(max_candidates, name="max_candidates")
    excluded = _normalized_exclusions(exclude_sha256)
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
            (candidate_limit,),
        ).fetchall()
    finally:
        db.close()

    for sha256, raw_path, analysis_path, sampled_states in rows:
        if not isinstance(sha256, str):
            continue
        normalized_sha = sha256.strip().lower()
        if (
            len(normalized_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in normalized_sha)
            or normalized_sha in excluded
        ):
            continue
        sampled_state_count = _native_int(sampled_states)
        if sampled_state_count is None:
            continue
        candidate = _healthy_candidate(
            sha256=str(sha256),
            raw_path=str(raw_path),
            analysis_path=str(analysis_path),
            sampled_states=sampled_state_count,
        )
        if candidate is not None:
            return {
                **candidate,
                "excluded_sha256_count": len(excluded),
            }

    raise RuntimeError(
        "No healthy role-resolved 4v4 replay found among "
        f"{min(len(rows), candidate_limit)} candidates "
        f"after excluding {len(excluded)} replay(s)"
    )



def select_scenario_sources(
    db_path: Path,
    *,
    count: int = 3,
    max_candidates: int = 1000,
    exclude_sha256: set[str] | None = None,
) -> dict[str, Any]:
    """Select a deterministic, disjoint set of healthy replay sources.

    Selection deliberately reuses the single-source selector and expands its
    exclusion set after every pick. This preserves the established ordering
    contract (sampled states descending, SHA-256 ascending) while guaranteeing
    that no replay can appear twice in one frozen evaluation suite.
    """
    requested = _positive_native_int(count, name="count")
    candidate_limit = _positive_native_int(
        max_candidates,
        name="max_candidates",
    )
    initial_excluded = _normalized_exclusions(exclude_sha256)
    excluded = set(initial_excluded)
    selected: list[dict[str, Any]] = []

    for _ in range(requested):
        candidate = select_scenario_source(
            db_path,
            max_candidates=candidate_limit,
            exclude_sha256=excluded,
        )
        replay_sha = str(candidate.get("sha256") or "").strip().lower()
        if len(replay_sha) != 64:
            raise RuntimeError("selected replay source has invalid SHA-256")
        source = {
            key: value
            for key, value in candidate.items()
            if key != "excluded_sha256_count"
        }
        selected.append(source)
        excluded.add(replay_sha)

    return {
        "schema": "haxlab-replay-scenario-source-set-v2",
        "selection_algorithm": "state-pass-v4-sampled-states-desc-sha256-asc",
        "requested_source_count": requested,
        "source_count": len(selected),
        "initial_excluded_sha256_count": len(initial_excluded),
        "sources": selected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-scenario-source")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument("--max-candidates", type=int, default=1000)
    parser.add_argument(
        "--count",
        type=int,
        default=1,
        help="Number of disjoint replay sources to select. Values >1 emit a v2 source-set manifest.",
    )
    parser.add_argument(
        "--exclude-sha256",
        action="append",
        default=[],
        help="Replay SHA-256 to exclude. Repeatable.",
    )
    parser.add_argument(
        "--exclude-sha256-file",
        type=Path,
        default=None,
        help="Optional newline-delimited replay SHA-256 exclusion file.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    excluded = set(args.exclude_sha256)
    if args.exclude_sha256_file is not None:
        excluded.update(
            line.strip()
            for line in args.exclude_sha256_file.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        )

    try:
        if args.count == 1:
            result = select_scenario_source(
                args.state_db,
                max_candidates=args.max_candidates,
                exclude_sha256=excluded,
            )
        else:
            result = select_scenario_sources(
                args.state_db,
                count=args.count,
                max_candidates=args.max_candidates,
                exclude_sha256=excluded,
            )
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
