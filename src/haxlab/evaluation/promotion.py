from __future__ import annotations

import math
from typing import Any

from haxlab.evaluation.models import (
    EvaluationEvidence,
    PromotionDecision,
    PromotionPolicy,
    Regression,
)


def _finite_number(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool):
        failures.append(f"invalid_evidence:{label}:boolean")
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        failures.append(f"invalid_evidence:{label}:non_numeric")
        return 0.0
    if not math.isfinite(number):
        failures.append(f"invalid_evidence:{label}:non_finite")
        return 0.0
    if minimum is not None and number < minimum:
        failures.append(
            f"invalid_evidence:{label}:below_minimum:"
            f"{number:.6f}<{minimum:.6f}"
        )
    if maximum is not None and number > maximum:
        failures.append(
            f"invalid_evidence:{label}:above_maximum:"
            f"{number:.6f}>{maximum:.6f}"
        )
    return number


def _integer(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: int = 0,
) -> int:
    number = _finite_number(
        failures,
        value,
        label,
        minimum=float(minimum),
    )
    if not number.is_integer():
        failures.append(f"invalid_evidence:{label}:not_integer")
        return 0
    return int(number)


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


def _validated_regressions(
    failures: list[str],
    value: Any,
) -> tuple[Regression, ...]:
    if type(value) is not tuple:
        failures.append("invalid_evidence:regressions:not_tuple")
        return ()

    valid: list[Regression] = []
    for index, item in enumerate(value):
        if type(item) is not Regression:
            failures.append(
                f"invalid_evidence:regressions:{index}:object_type"
            )
            continue

        if type(item.scenario) is not str or not item.scenario.strip():
            failures.append(
                f"invalid_evidence:regressions:{index}:scenario"
            )
            continue
        if type(item.severity) is not str or not item.severity.strip():
            failures.append(
                f"invalid_evidence:regressions:{index}:severity"
            )
            continue
        if type(item.details) is not str:
            failures.append(
                f"invalid_evidence:regressions:{index}:details"
            )
            continue

        valid.append(item)

    return tuple(valid)


def decide_promotion(
    evidence: EvaluationEvidence,
    policy: PromotionPolicy = PromotionPolicy(),
) -> PromotionDecision:
    """Conservative fail-closed gate for challenger -> champion promotion."""
    if not isinstance(evidence, EvaluationEvidence):
        return PromotionDecision(
            promote=False,
            reasons=("invalid_evidence:object_type",),
        )
    if not isinstance(policy, PromotionPolicy):
        return PromotionDecision(
            promote=False,
            reasons=("invalid_policy:object_type",),
        )

    failures: list[str] = []

    minimum_games = _policy_integer(
        failures,
        policy.minimum_games,
        "minimum_games",
        minimum=1,
    )
    minimum_score_rate_lower_bound = _policy_number(
        failures,
        policy.minimum_score_rate_lower_bound,
        "minimum_score_rate_lower_bound",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_scenario_pass_rate = _policy_number(
        failures,
        policy.minimum_scenario_pass_rate,
        "minimum_scenario_pass_rate",
        minimum=0.0,
        maximum=1.0,
    )
    allow_critical_regressions = _policy_bool(
        failures,
        policy.allow_critical_regressions,
        "allow_critical_regressions",
    )

    challenger_id = str(evidence.challenger_id or "").strip()
    champion_id = str(evidence.champion_id or "").strip()
    if not challenger_id:
        failures.append("missing_challenger_id")
    if not champion_id:
        failures.append("missing_champion_id")
    if challenger_id and challenger_id == champion_id:
        failures.append("challenger_matches_champion")

    games = _integer(
        failures,
        evidence.games_vs_champion,
        "games_vs_champion",
    )
    score_rate = _finite_number(
        failures,
        evidence.score_rate_vs_champion,
        "score_rate_vs_champion",
        minimum=0.0,
        maximum=1.0,
    )
    lower_bound = _finite_number(
        failures,
        evidence.score_rate_lower_bound,
        "score_rate_lower_bound",
        minimum=0.0,
        maximum=1.0,
    )
    scenario_total = _integer(
        failures,
        evidence.frozen_scenarios_total,
        "frozen_scenarios_total",
    )
    scenario_passed = _integer(
        failures,
        evidence.frozen_scenarios_passed,
        "frozen_scenarios_passed",
    )
    regressions = _validated_regressions(failures, evidence.regressions)

    if evidence.goal_difference_per_game is not None:
        _finite_number(
            failures,
            evidence.goal_difference_per_game,
            "goal_difference_per_game",
        )

    if lower_bound > score_rate:
        failures.append(
            "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"
        )

    if games < minimum_games:
        failures.append(
            f"insufficient_games:{games}<{minimum_games}"
        )

    if lower_bound < minimum_score_rate_lower_bound:
        failures.append(
            "head_to_head_confidence_gate_failed:"
            f"{lower_bound:.4f}"
            f"<{minimum_score_rate_lower_bound:.4f}"
        )

    if scenario_passed > scenario_total:
        failures.append(
            "invalid_evidence:frozen_scenarios_passed_exceeds_total:"
            f"{scenario_passed}>{scenario_total}"
        )

    if scenario_total <= 0:
        failures.append("no_frozen_scenarios")
    else:
        pass_rate = scenario_passed / scenario_total
        if pass_rate < minimum_scenario_pass_rate:
            failures.append(
                f"scenario_pass_rate_failed:{pass_rate:.4f}"
                f"<{minimum_scenario_pass_rate:.4f}"
            )

    critical = [
        regression
        for regression in regressions
        if regression.severity.casefold() == "critical"
    ]
    if critical and not allow_critical_regressions:
        failures.append(
            "critical_regressions:"
            + ",".join(item.scenario for item in critical)
        )

    if evidence.reproducible is not True:
        failures.append("run_not_reproducible")

    if failures:
        return PromotionDecision(promote=False, reasons=tuple(failures))

    return PromotionDecision(
        promote=True,
        reasons=(
            "head_to_head_gate_passed",
            "frozen_scenarios_passed",
            "no_blocking_regressions",
            "run_reproducible",
        ),
    )
