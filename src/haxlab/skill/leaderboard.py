from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import math
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median, pstdev

from haxlab.analysis.roles import infer_roles_4v4, role_map_4v4
from haxlab.skill.estimator import estimate_player_skill_v0
from haxlab.skill.models import PerformanceVector, SkillObservation


DIMENSION_WEIGHTS = {
    "retention": 0.18,
    "progression": 0.14,
    "creation": 0.14,
    "finishing": 0.14,
    "defending": 0.14,
    "positioning": 0.08,
    "pressure_recovery": 0.09,
    "risk_management": 0.09,
}


def _name_key(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = " ".join(str(name).strip().split())
    return cleaned.casefold() or None


def _load_aliases(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if aliases is None and isinstance(payload, dict):
        aliases = payload
    result: dict[str, str] = {}
    for source, target in (aliases or {}).items():
        source_key = _name_key(str(source))
        target_key = _name_key(str(target))
        if source_key and target_key:
            result[source_key] = target_key
    return result


def _identity_key(
    player: dict,
    aliases: dict[str, str] | None = None,
) -> str | None:
    aliases = aliases or {}
    name = _name_key(player.get("name"))

    # Explicit human-confirmed aliases override replay auth identity. This is
    # intentional: an alias is used precisely when HaxLab has already split one
    # real player into multiple profiles.
    if name:
        target = aliases.get(name)
        if target:
            return f"alias:{target}"
        if name in set(aliases.values()):
            return f"alias:{name}"

    auth_hash = player.get("authHash")
    if auth_hash:
        return f"auth:{auth_hash}"
    return f"name:{name}" if name else None


def _safe_div(num: float, den: float) -> float | None:
    if den <= 0:
        return None
    return num / den


def _player_minutes(player: dict, payload: dict) -> float:
    sample_every = max(
        1,
        int((payload.get("simulation") or {}).get("sampleEveryTicks") or 6),
    )
    samples = int(player.get("samples") or 0)
    return samples * sample_every / 3600.0


def _role_map(players: list[dict]) -> dict[int, str]:
    """Compatibility wrapper for the fixed 4v4 GK/DM/AM/ST role model."""
    return role_map_4v4(players)


def _raw_metrics(
    player: dict,
    minutes: float,
    *,
    schema_version: int,
) -> dict[str, float | None]:
    use_touch_features = schema_version >= 4 and (
        "teamTouchTransfersOut" in player or "touches" in player
    )

    if use_touch_features:
        team_transfers = int(player.get("teamTouchTransfersOut") or 0)
        self_retouches = int(player.get("selfRetouches") or 0)
        retained = team_transfers + self_retouches
        lost = int(player.get("turnovers") or 0)
        recoveries = int(player.get("recoveries") or 0)
        goals = int(player.get("touchGoals") or 0)
        assists = int(player.get("touchAssists") or 0)
        progression_events = int(player.get("touchProgressionEvents") or 0)
        progression_sum = float(player.get("touchProgressionSum") or 0.0)
        pressured_transitions = int(player.get("pressuredTransitions") or 0)
        retained_under_pressure = int(player.get("retainedUnderPressure") or 0)
        pressure_recovery = (
            retained_under_pressure / pressured_transitions
            if pressured_transitions >= 3
            else None
        )
    else:
        retained = int(player.get("inferredRetainedChains") or 0)
        team_transfers = retained
        lost = int(player.get("inferredLostChains") or 0)
        recoveries = int(player.get("inferredRecoveries") or 0)
        goals = int(player.get("inferredGoals") or 0)
        assists = int(player.get("inferredAssists") or 0)
        progression_events = int(player.get("progressionEvents") or 0)
        progression_sum = float(player.get("progressionSum") or 0.0)
        samples = int(player.get("samples") or 0)
        close = int(player.get("closeBallSamples") or 0)
        pressure_recovery = close / samples if samples >= 30 else None

    transitions = retained + lost
    samples = int(player.get("samples") or 0)
    near = int(player.get("nearestBallSamples") or 0)

    return {
        "retention": retained / transitions if transitions >= 3 else None,
        "progression": (
            progression_sum / progression_events
            if progression_events >= 3
            else None
        ),
        "creation": (
            (assists * 10.0 / minutes) + (team_transfers / minutes) * 0.05
            if minutes >= 0.5
            else None
        ),
        "finishing": goals * 10.0 / minutes if minutes >= 0.5 else None,
        "defending": recoveries / minutes if minutes >= 0.5 else None,
        "positioning": near / samples if samples >= 30 else None,
        "pressure_recovery": pressure_recovery,
        "risk_management": -(lost / minutes) if minutes >= 0.5 else None,
    }


def _true_4v4_average_sizes(payload: dict) -> dict[int, float] | None:
    simulation = payload.get("simulation") or {}
    sampled_state_count = int(simulation.get("sampledStateCount") or 0)
    if sampled_state_count <= 0:
        return None

    players = list(payload.get("players") or [])
    result: dict[int, float] = {}
    for team_id in (1, 2):
        team_sample_total = sum(
            int(player.get("samples") or 0)
            for player in players
            if int(player.get("teamId") or 0) == team_id
        )
        result[team_id] = team_sample_total / sampled_state_count

    if all(3.5 <= result[team_id] <= 4.5 for team_id in (1, 2)):
        return result
    return None


def load_match_evidence(
    root: Path,
    aliases: dict[str, str] | None = None,
) -> list[dict]:
    evidence: list[dict] = []
    aliases = aliases or {}

    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        schema_version = int(payload.get("schemaVersion") or 0)
        if schema_version not in (3, 4):
            continue
        if _true_4v4_average_sizes(payload) is None:
            continue

        players = list(payload.get("players") or [])
        role_evidence = infer_roles_4v4(players)
        roles = {player_id: row["role"] for player_id, row in role_evidence.items()}
        total_frames = int(payload.get("totalFrames") or 0)
        match_minutes = total_frames / 3600.0

        for player in players:
            key = _identity_key(player, aliases)
            if key is None:
                continue
            minutes = _player_minutes(player, payload)
            if minutes <= 0:
                continue
            evidence.append(
                {
                    "player_id": key,
                    "match_id": path.stem,
                    "team_id": int(player.get("teamId") or 0),
                    "name": " ".join(str(player.get("name")).strip().split()),
                    "role": roles.get(int(player.get("id") or -1), "unknown"),
                    "role_confidence": float(
                        role_evidence.get(int(player.get("id") or -1), {}).get(
                            "confidence", 0.0
                        )
                    ),
                    "minutes": minutes,
                    "match_minutes": match_minutes,
                    "schema_version": schema_version,
                    "metrics": _raw_metrics(
                        player,
                        minutes,
                        schema_version=schema_version,
                    ),
                }
            )

    return evidence


def _normalizers(evidence: list[dict]) -> dict[tuple[str, str], tuple[float, float]]:
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    global_values: dict[str, list[float]] = defaultdict(list)

    for row in evidence:
        # Very short appearances can create extreme per-minute values. Keep the
        # evidence for the player estimate, but do not let it define the
        # population baseline used to normalize everybody else.
        if float(row.get("minutes", 0.0)) < 1.0:
            continue

        role = row["role"]
        for dimension, value in row["metrics"].items():
            if value is None or not math.isfinite(value):
                continue
            values[(role, dimension)].append(float(value))
            global_values[dimension].append(float(value))

    result: dict[tuple[str, str], tuple[float, float]] = {}
    roles = {row["role"] for row in evidence}
    for role in roles:
        for dimension in DIMENSION_WEIGHTS:
            local = values.get((role, dimension), [])
            source = local if len(local) >= 30 else global_values.get(dimension, [])
            if not source:
                continue

            center = median(source)
            absolute_deviations = [abs(value - center) for value in source]
            mad = median(absolute_deviations)
            robust_scale = 1.4826 * mad

            # Some discrete/sparse metrics have MAD=0. Fall back to standard
            # deviation only for scale, while keeping the robust median center.
            if robust_scale <= 1e-9:
                fallback = pstdev(source)
                robust_scale = fallback if fallback > 1e-9 else 1.0

            result[(role, dimension)] = (center, robust_scale)
    return result


def _normalize(
    role: str,
    dimension: str,
    value: float | None,
    normalizers: dict[tuple[str, str], tuple[float, float]],
) -> float | None:
    if value is None:
        return None
    params = normalizers.get((role, dimension))
    if params is None:
        return None
    avg, sd = params
    return max(-3.0, min(3.0, (float(value) - avg) / sd))


def _mean_or_zero(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _bounded_match_contexts(
    rows: list[dict],
) -> dict[tuple[str, str], tuple[float, float]]:
    """Estimate small teammate/opponent context from individual action evidence.

    Context is deliberately derived from normalized player actions, not match
    results. A prior toward zero and a hard [-1, 1] bound keep it subordinate
    to the player's own observed performance.
    """
    strength_sum: defaultdict[str, float] = defaultdict(float)
    strength_weight: defaultdict[str, float] = defaultdict(float)

    for row in rows:
        values = [
            float(value)
            for value in row["normalized"].values()
            if value is not None and math.isfinite(value)
        ]
        if not values:
            continue
        exposure = min(2.0, max(0.25, math.sqrt(row["minutes"] / 3.0)))
        strength_sum[row["player_id"]] += mean(values) * exposure
        strength_weight[row["player_id"]] += exposure

    player_strength: dict[str, float] = {}
    prior_weight = 6.0
    for player_id, total in strength_sum.items():
        estimate = total / (strength_weight[player_id] + prior_weight)
        player_strength[player_id] = max(-2.0, min(2.0, estimate))

    by_match_team: defaultdict[tuple[str, int], set[str]] = defaultdict(set)
    for row in rows:
        team_id = int(row["team_id"])
        if team_id in (1, 2):
            by_match_team[(row["match_id"], team_id)].add(row["player_id"])

    result: dict[tuple[str, str], tuple[float, float]] = {}
    for row in rows:
        player_id = row["player_id"]
        match_id = row["match_id"]
        team_id = int(row["team_id"])
        if team_id not in (1, 2):
            result[(match_id, player_id)] = (0.0, 0.0)
            continue

        teammate_ids = by_match_team.get((match_id, team_id), set()) - {player_id}
        opponent_ids = by_match_team.get((match_id, 3 - team_id), set())

        teammate_raw = _mean_or_zero(
            [player_strength.get(item, 0.0) for item in teammate_ids]
        )
        opponent_raw = _mean_or_zero(
            [player_strength.get(item, 0.0) for item in opponent_ids]
        )

        # Scale a +/-2 preliminary action strength into the estimator's
        # intentionally small +/-1 context range.
        teammate_context = max(-1.0, min(1.0, teammate_raw / 2.0))
        opponent_context = max(-1.0, min(1.0, opponent_raw / 2.0))
        result[(match_id, player_id)] = (teammate_context, opponent_context)

    return result


def build_leaderboard(
    root: Path,
    aliases: dict[str, str] | None = None,
) -> list[dict]:
    evidence = load_match_evidence(root, aliases)
    normalizers = _normalizers(evidence)

    normalized_rows: list[dict] = []
    for row in evidence:
        normalized_rows.append(
            {
                **row,
                "normalized": {
                    dimension: _normalize(
                        row["role"],
                        dimension,
                        row["metrics"].get(dimension),
                        normalizers,
                    )
                    for dimension in DIMENSION_WEIGHTS
                },
            }
        )

    contexts = _bounded_match_contexts(normalized_rows)

    observations: list[SkillObservation] = []
    name_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    matches: Counter[str] = Counter()
    minutes: defaultdict[str, float] = defaultdict(float)
    role_minutes: defaultdict[str, Counter[str]] = defaultdict(Counter)
    teammate_contexts: defaultdict[str, list[float]] = defaultdict(list)
    opponent_contexts: defaultdict[str, list[float]] = defaultdict(list)

    for row in normalized_rows:
        player_id = row["player_id"]
        name_counts[player_id][row["name"]] += 1
        matches[player_id] += 1
        minutes[player_id] += row["minutes"]
        role_minutes[player_id][row["role"]] += row["minutes"]

        teammate_context, opponent_context = contexts.get(
            (row["match_id"], player_id),
            (0.0, 0.0),
        )
        teammate_contexts[player_id].append(teammate_context)
        opponent_contexts[player_id].append(opponent_context)

        match_quality = min(1.0, max(0.15, row["match_minutes"] / 3.0))
        exposure = min(2.0, max(0.25, math.sqrt(row["minutes"] / 3.0)))

        observations.append(
            SkillObservation(
                player_id=player_id,
                performance=PerformanceVector(**row["normalized"]),
                match_quality_weight=match_quality,
                minutes_or_possessions_weight=exposure,
                teammate_context=teammate_context,
                opponent_context=opponent_context,
                role=row["role"],
            )
        )

    result: list[dict] = []
    by_player: defaultdict[str, list[SkillObservation]] = defaultdict(list)
    for observation in observations:
        by_player[observation.player_id].append(observation)

    for player_id, own in by_player.items():
        estimate = estimate_player_skill_v0(
            player_id,
            own,
            prior_mean=0.0,
            prior_weight=8.0,
        )

        weighted_sum = 0.0
        weight_sum = 0.0
        uncertainty_sum = 0.0
        uncertainty_weight = 0.0
        dimensions: dict[str, dict] = {}

        for dimension, weight in DIMENSION_WEIGHTS.items():
            dim = estimate.dimensions[dimension]
            dimensions[dimension] = asdict(dim)
            if dim.effective_weight <= 0:
                continue
            weighted_sum += dim.mean * weight
            weight_sum += weight
            uncertainty_sum += dim.uncertainty * weight
            uncertainty_weight += weight

        overall_z = weighted_sum / weight_sum if weight_sum else 0.0
        uncertainty = (
            uncertainty_sum / uncertainty_weight
            if uncertainty_weight
            else 1.0
        )
        rating = max(20.0, min(80.0, 50.0 + 10.0 * overall_z))
        rating_uncertainty = 10.0 * uncertainty
        primary_role = (
            role_minutes[player_id].most_common(1)[0][0]
            if role_minutes[player_id]
            else "unknown"
        )

        result.append(
            {
                "player_id": player_id,
                "name": name_counts[player_id].most_common(1)[0][0],
                "matches": matches[player_id],
                "minutes": minutes[player_id],
                "role": primary_role,
                "rating": rating,
                "rating_uncertainty": rating_uncertainty,
                "overall_z": overall_z,
                "average_teammate_context": _mean_or_zero(
                    teammate_contexts[player_id]
                ),
                "average_opponent_context": _mean_or_zero(
                    opponent_contexts[player_id]
                ),
                "dimensions": dimensions,
            }
        )

    result.sort(
        key=lambda row: (
            row["rating"],
            -row["rating_uncertainty"],
            row["matches"],
        ),
        reverse=True,
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-skill")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--min-matches", type=int, default=20)
    parser.add_argument("--min-minutes", type=float, default=60.0)
    parser.add_argument(
        "--aliases",
        type=Path,
        default=Path("/opt/haxlab/configs/player_aliases.json"),
        help="Optional JSON file of confirmed display-name aliases.",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional path to write the complete leaderboard snapshot as JSON.",
    )
    args = parser.parse_args()

    aliases = _load_aliases(args.aliases if args.aliases.exists() else None)
    rows = [
        row
        for row in build_leaderboard(args.root, aliases)
        if row["matches"] >= max(1, args.min_matches)
        and row["minutes"] >= max(0.0, args.min_minutes)
    ][: max(1, args.top)]

    snapshot = {
        "schema": "haxlab-skill-leaderboard-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(args.root),
        "aliases_path": str(args.aliases) if args.aliases.exists() else None,
        "alias_count": len(aliases),
        "min_matches": max(1, args.min_matches),
        "min_minutes": max(0.0, args.min_minutes),
        "rows": rows,
    }

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    if args.format == "json":
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0

    print(
        "HAXLAB SKILL V0.3 — experimental role-normalized performance evidence; "
        "not yet a definitive player ranking."
    )
    print(
        "Uses robust role-normalized touch-chain evidence when schema v4 is available, with v3 "
        "kick-chain fallback. Rating includes conservative shrinkage and uncertainty."
    )
    print()

    header = (
        f"{'#':>3}  {'Player':<23} {'Role':<9} {'M':>5} {'Min':>8} "
        f"{'Rating':>8} {'±':>6} {'Ret':>7} {'Prog':>7} {'Def':>7}"
    )
    print(header)
    print("-" * len(header))

    for index, row in enumerate(rows, 1):
        dims = row["dimensions"]
        print(
            f"{index:>3}  {row['name'][:23]:<23} "
            f"{row['role'][:9]:<9} "
            f"{row['matches']:>5} "
            f"{row['minutes']:>8.1f} "
            f"{row['rating']:>8.2f} "
            f"{row['rating_uncertainty']:>6.2f} "
            f"{dims['retention']['mean']:>7.2f} "
            f"{dims['progression']['mean']:>7.2f} "
            f"{dims['defending']['mean']:>7.2f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
