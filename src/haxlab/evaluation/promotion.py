from __future__ import annotations

from haxlab.evaluation.models import (
    EvaluationEvidence,
    PromotionDecision,
    PromotionPolicy,
)


def decide_promotion(
    evidence: EvaluationEvidence,
    policy: PromotionPolicy = PromotionPolicy(),
) -> PromotionDecision:
    """Conservative gate for challenger -> champion promotion."""
    failures: list[str] = []

    if evidence.games_vs_champion < policy.minimum_games:
        failures.append(
            f"insufficient_games:{evidence.games_vs_champion}<{policy.minimum_games}"
        )

    if evidence.score_rate_lower_bound < policy.minimum_score_rate_lower_bound:
        failures.append(
            "head_to_head_confidence_gate_failed:"
            f"{evidence.score_rate_lower_bound:.4f}"
            f"<{policy.minimum_score_rate_lower_bound:.4f}"
        )

    if evidence.frozen_scenarios_total <= 0:
        failures.append("no_frozen_scenarios")
    else:
        pass_rate = (
            evidence.frozen_scenarios_passed / evidence.frozen_scenarios_total
        )
        if pass_rate < policy.minimum_scenario_pass_rate:
            failures.append(
                f"scenario_pass_rate_failed:{pass_rate:.4f}"
                f"<{policy.minimum_scenario_pass_rate:.4f}"
            )

    critical = [
        regression
        for regression in evidence.regressions
        if regression.severity.casefold() == "critical"
    ]
    if critical and not policy.allow_critical_regressions:
        failures.append(
            "critical_regressions:" + ",".join(item.scenario for item in critical)
        )

    if not evidence.reproducible:
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
