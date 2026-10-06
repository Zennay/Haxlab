from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DuelGatePolicy:
    minimum_matches: int = 12
    minimum_match_score: float = 0.55
    minimum_territory_share: float = 0.50
    minimum_attack_third_share: float = 0.45
    maximum_side_territory_gap: float = 0.20
    maximum_runtime_errors: int = 0
    maximum_kick_action_rate: float = 0.10
    minimum_replay_progression_share: float = 0.42
    minimum_replay_nonzero_movement_rate: float = 0.10
    require_replay_kick_activity: bool = True


@dataclass(frozen=True)
class DuelGateDecision:
    eligible_to_replace_champion: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def _share(for_rate: float, against_rate: float) -> float:
    total = for_rate + against_rate
    return for_rate / total if total > 0 else 0.5


def _required_number(
    failures: list[str],
    mapping: dict[str, Any],
    key: str,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if key not in mapping:
        failures.append(f"invalid_metric:{label}:missing")
        return 0.0

    value = mapping.get(key)
    if isinstance(value, bool):
        failures.append(f"invalid_metric:{label}:boolean")
        return 0.0
    if not isinstance(value, (int, float)):
        failures.append(f"invalid_metric:{label}:non_numeric")
        return 0.0

    number = float(value)

    if not math.isfinite(number):
        failures.append(f"invalid_metric:{label}:non_finite")
        return 0.0
    if minimum is not None and number < minimum:
        failures.append(
            f"invalid_metric:{label}:below_minimum:{number:.6f}<{minimum:.6f}"
        )
    if maximum is not None and number > maximum:
        failures.append(
            f"invalid_metric:{label}:above_maximum:{number:.6f}>{maximum:.6f}"
        )
    return number


def _required_integer(
    failures: list[str],
    mapping: dict[str, Any],
    key: str,
    label: str,
    *,
    minimum: int | None = None,
) -> int:
    if key not in mapping:
        failures.append(f"invalid_metric:{label}:missing")
        return 0

    value = mapping.get(key)
    if type(value) is not int:
        failures.append(f"invalid_metric:{label}:not_integer")
        return 0
    if minimum is not None and value < minimum:
        failures.append(
            f"invalid_metric:{label}:below_minimum:{value}<{minimum}"
        )
    return value


def _policy_number(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        failures.append(f"invalid_policy:{label}:non_numeric")
        return minimum
    number = float(value)
    if not math.isfinite(number):
        failures.append(f"invalid_policy:{label}:non_finite")
        return minimum
    if number < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:"
            f"{number:.6f}<{minimum:.6f}"
        )
    if number > maximum:
        failures.append(
            f"invalid_policy:{label}:above_maximum:"
            f"{number:.6f}>{maximum:.6f}"
        )
    return number


def _policy_integer(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        failures.append(f"invalid_policy:{label}:non_integer")
        return minimum
    if value < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:{value}<{minimum}"
        )
    return value


def _policy_bool(
    failures: list[str],
    value: Any,
    label: str,
) -> bool:
    if type(value) is not bool:
        failures.append(f"invalid_policy:{label}:not_boolean")
        return False
    return value


def decide_duel_gate(
    duel: dict[str, Any],
    policy: DuelGatePolicy = DuelGatePolicy(),
) -> DuelGateDecision:
    if not isinstance(duel, dict):
        return DuelGateDecision(
            eligible_to_replace_champion=False,
            reasons=("invalid_duel:object_type",),
            checks={},
        )
    if not isinstance(policy, DuelGatePolicy):
        return DuelGateDecision(
            eligible_to_replace_champion=False,
            reasons=("invalid_policy:object_type",),
            checks={},
        )

    failures: list[str] = []
    checks: dict[str, Any] = {}

    minimum_matches = _policy_integer(
        failures,
        policy.minimum_matches,
        "minimum_matches",
        minimum=1,
    )
    minimum_match_score = _policy_number(
        failures,
        policy.minimum_match_score,
        "minimum_match_score",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_territory_share = _policy_number(
        failures,
        policy.minimum_territory_share,
        "minimum_territory_share",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_attack_third_share = _policy_number(
        failures,
        policy.minimum_attack_third_share,
        "minimum_attack_third_share",
        minimum=0.0,
        maximum=1.0,
    )
    maximum_side_territory_gap = _policy_number(
        failures,
        policy.maximum_side_territory_gap,
        "maximum_side_territory_gap",
        minimum=0.0,
        maximum=1.0,
    )
    maximum_runtime_errors = _policy_integer(
        failures,
        policy.maximum_runtime_errors,
        "maximum_runtime_errors",
        minimum=0,
    )
    maximum_kick_action_rate = _policy_number(
        failures,
        policy.maximum_kick_action_rate,
        "maximum_kick_action_rate",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_replay_progression_share = _policy_number(
        failures,
        policy.minimum_replay_progression_share,
        "minimum_replay_progression_share",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_replay_nonzero_movement_rate = _policy_number(
        failures,
        policy.minimum_replay_nonzero_movement_rate,
        "minimum_replay_nonzero_movement_rate",
        minimum=0.0,
        maximum=1.0,
    )
    require_replay_kick_activity = _policy_bool(
        failures,
        policy.require_replay_kick_activity,
        "require_replay_kick_activity",
    )

    schema_value = duel.get("schema")
    schema = schema_value.strip() if type(schema_value) is str else ""
    if type(schema_value) is not str or not schema:
        failures.append("invalid_duel:schema")
    supported_schemas = {
        "haxlab-elite-model-duel-v1",
        "haxlab-elite-replay-seeded-duel-v1",
    }
    checks["evaluation_schema"] = schema
    checks["evaluation_mode"] = duel.get("evaluation_mode")
    if schema not in supported_schemas:
        failures.append(f"unsupported_duel_schema:{schema or 'missing'}")

    if schema == "haxlab-elite-replay-seeded-duel-v1":
        if duel.get("evaluation_mode") != "replay_seeded_proxy_v1":
            failures.append(
                "unsupported_replay_duel_mode:"
                f"{duel.get('evaluation_mode') or 'missing'}"
            )
        scenario_sha_value = duel.get("scenario_sha256")
        scenario_sha = (
            scenario_sha_value.lower()
            if type(scenario_sha_value) is str
            else ""
        )
        checks["scenario_sha256"] = scenario_sha
        if (
            len(scenario_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in scenario_sha)
        ):
            failures.append("invalid_scenario_sha256")
        challenger_model = duel.get("challenger_model")
        champion_model = duel.get("champion_model")
        if type(challenger_model) is not str or not challenger_model.strip():
            failures.append("invalid_challenger_model")
        if type(champion_model) is not str or not champion_model.strip():
            failures.append("invalid_champion_model")

    matches = _required_integer(
        failures,
        duel,
        "matches",
        "matches",
        minimum=0,
    )
    wins = _required_integer(
        failures,
        duel,
        "wins",
        "wins",
        minimum=0,
    )
    draws = _required_integer(
        failures,
        duel,
        "draws",
        "draws",
        minimum=0,
    )
    losses = _required_integer(
        failures,
        duel,
        "losses",
        "losses",
        minimum=0,
    )
    checks["matches"] = matches
    checks["wins"] = wins
    checks["draws"] = draws
    checks["losses"] = losses

    if wins + draws + losses != matches:
        failures.append(
            "match_tally_mismatch:"
            f"{wins}+{draws}+{losses}!={matches}"
        )
    if matches < minimum_matches:
        failures.append(
            f"insufficient_matches:{matches}<{minimum_matches}"
        )

    match_score = (wins + 0.5 * draws) / matches if matches > 0 else 0.0
    checks["match_score"] = match_score
    if match_score < minimum_match_score:
        failures.append(
            f"match_score:{match_score:.4f}<{minimum_match_score:.4f}"
        )

    territory = duel.get("territory") or {}
    if not isinstance(territory, dict):
        failures.append("invalid_territory_payload")
        territory = {}

    challenger_half = _required_number(
        failures,
        territory,
        "challenger_half_rate",
        "territory:challenger_half_rate",
        minimum=0.0,
        maximum=1.0,
    )
    champion_half = _required_number(
        failures,
        territory,
        "champion_half_rate",
        "territory:champion_half_rate",
        minimum=0.0,
        maximum=1.0,
    )
    territory_share = _share(challenger_half, champion_half)
    checks["territory_share"] = territory_share
    if territory_share < minimum_territory_share:
        failures.append(
            "territory_share:"
            f"{territory_share:.4f}<{minimum_territory_share:.4f}"
        )

    challenger_attack = _required_number(
        failures,
        territory,
        "challenger_attack_third_rate",
        "territory:challenger_attack_third_rate",
        minimum=0.0,
        maximum=1.0,
    )
    champion_attack = _required_number(
        failures,
        territory,
        "champion_attack_third_rate",
        "territory:champion_attack_third_rate",
        minimum=0.0,
        maximum=1.0,
    )
    attack_share = _share(challenger_attack, champion_attack)
    checks["attack_third_share"] = attack_share
    if attack_share < minimum_attack_third_share:
        failures.append(
            "attack_third_share:"
            f"{attack_share:.4f}<{minimum_attack_third_share:.4f}"
        )

    challenger_errors = _required_integer(
        failures,
        duel,
        "challenger_runtime_errors",
        "challenger_runtime_errors",
        minimum=0,
    )
    champion_errors = _required_integer(
        failures,
        duel,
        "champion_runtime_errors",
        "champion_runtime_errors",
        minimum=0,
    )
    checks["challenger_runtime_errors"] = challenger_errors
    checks["champion_runtime_errors"] = champion_errors
    if challenger_errors > maximum_runtime_errors:
        failures.append(
            "challenger_runtime_errors:"
            f"{challenger_errors}>{maximum_runtime_errors}"
        )
    if champion_errors > maximum_runtime_errors:
        failures.append(
            "champion_runtime_errors:"
            f"{champion_errors}>{maximum_runtime_errors}"
        )

    kick_rate = _required_number(
        failures,
        duel,
        "challenger_kick_action_rate",
        "challenger_kick_action_rate",
        minimum=0.0,
        maximum=1.0,
    )
    checks["challenger_kick_action_rate"] = kick_rate
    if kick_rate > maximum_kick_action_rate:
        failures.append(
            "challenger_kick_action_rate:"
            f"{kick_rate:.4f}>{maximum_kick_action_rate:.4f}"
        )

    if schema == "haxlab-elite-replay-seeded-duel-v1":
        progression_share = _required_number(
            failures,
            duel,
            "challenger_progression_share",
            "challenger_progression_share",
            minimum=0.0,
            maximum=1.0,
        )
        movement_rate = _required_number(
            failures,
            duel,
            "challenger_nonzero_movement_rate",
            "challenger_nonzero_movement_rate",
            minimum=0.0,
            maximum=1.0,
        )
        checks["challenger_progression_share"] = progression_share
        checks["challenger_nonzero_movement_rate"] = movement_rate
        if progression_share < minimum_replay_progression_share:
            failures.append(
                "challenger_progression_share:"
                f"{progression_share:.4f}<"
                f"{minimum_replay_progression_share:.4f}"
            )
        if movement_rate < minimum_replay_nonzero_movement_rate:
            failures.append(
                "challenger_nonzero_movement_rate:"
                f"{movement_rate:.4f}<"
                f"{minimum_replay_nonzero_movement_rate:.4f}"
            )
        if require_replay_kick_activity and kick_rate <= 0.0:
            failures.append("challenger_no_kick_activity")

    sides = duel.get("by_challenger_side") or {}
    if not isinstance(sides, dict):
        failures.append("invalid_side_metrics_payload")
        sides = {}
    side_rates: dict[str, float] = {}
    for side in ("1", "2"):
        row = sides.get(side)
        if not isinstance(row, dict) or not row:
            failures.append(f"missing_side_metrics:{side}")
            continue
        side_rates[side] = _required_number(
            failures,
            row,
            "challenger_half_rate",
            f"side_{side}:challenger_half_rate",
            minimum=0.0,
            maximum=1.0,
        )
    checks["side_challenger_half_rates"] = side_rates
    if len(side_rates) == 2:
        side_gap = abs(side_rates["1"] - side_rates["2"])
        checks["side_territory_gap"] = side_gap
        if side_gap > maximum_side_territory_gap:
            failures.append(
                "side_territory_gap:"
                f"{side_gap:.4f}>{maximum_side_territory_gap:.4f}"
            )

    goals = duel.get("goals") or {}
    if not isinstance(goals, dict):
        failures.append("invalid_goals_payload")
        goals = {}
    goal_diff = _required_integer(
        failures,
        goals,
        "differential",
        "goals:differential",
    )
    checks["goal_differential"] = goal_diff

    if failures:
        return DuelGateDecision(
            eligible_to_replace_champion=False,
            reasons=tuple(failures),
            checks=checks,
        )

    return DuelGateDecision(
        eligible_to_replace_champion=True,
        reasons=(
            "duel_match_score_passed",
            "duel_territory_passed",
            "duel_side_symmetry_passed",
            "duel_runtime_stable",
            "duel_kick_rate_passed",
        ),
        checks=checks,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-duel-gate")
    parser.add_argument("duel", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    duel = json.loads(args.duel.read_text(encoding="utf-8"))
    decision = decide_duel_gate(duel)
    result = {
        "schema": "haxlab-elite-duel-gate-v1",
        "duel_path": str(args.duel),
        "policy": asdict(DuelGatePolicy()),
        "eligible_to_replace_champion": decision.eligible_to_replace_champion,
        "reasons": list(decision.reasons),
        "checks": decision.checks,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["eligible_to_replace_champion"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
