"""Metamorphic, read-only promotion acceptance contract.

These tests exercise the original Arena-v2 promotion boundary with only in-memory
evidence.  They never read a live champion, mutate a policy or open a holdout.
"""
from dataclasses import replace

import pytest

from haxlab.evaluation.models import (
    EvaluationEvidence,
    PromotionPolicy,
    Regression,
)
from haxlab.evaluation.promotion import decide_promotion


def _passing_evidence() -> EvaluationEvidence:
    # Values deliberately sit on the inclusive default acceptance boundaries.
    return EvaluationEvidence(
        challenger_id="candidate-frozen",
        champion_id="champion-frozen",
        games_vs_champion=500,
        score_rate_vs_champion=0.55,
        score_rate_lower_bound=0.51,
        goal_difference_per_game=-0.1,
        frozen_scenarios_total=100,
        frozen_scenarios_passed=98,
        regressions=(),
        reproducible=True,
    )


def _assert_rejected_with(evidence, reason: str, policy=None) -> None:
    decision = decide_promotion(evidence, policy or PromotionPolicy())
    assert decision.promote is False
    assert any(item.startswith(reason) for item in decision.reasons), decision.reasons


def test_frozen_baseline_is_eligible_without_changing_inputs() -> None:
    evidence = _passing_evidence()
    policy = PromotionPolicy()
    before = repr((evidence, policy))
    first = decide_promotion(evidence, policy)
    assert first.promote is True
    assert first.reasons
    assert decide_promotion(evidence, policy) == first
    assert repr((evidence, policy)) == before


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"games_vs_champion": 499}, "insufficient_games:"),
        ({"score_rate_lower_bound": 0.50}, "head_to_head_confidence_gate_failed:"),
        ({"frozen_scenarios_passed": 97}, "scenario_pass_rate_failed:"),
        ({"reproducible": False}, "run_not_reproducible"),
        ({"reproducible": 1}, "run_not_reproducible"),
        ({"challenger_id": "champion-frozen"}, "challenger_matches_champion"),
        ({"score_rate_lower_bound": 0.56}, "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"),
        ({"frozen_scenarios_passed": 101}, "invalid_evidence:frozen_scenarios_passed_exceeds_total:"),
    ],
)
def test_one_deterioration_cannot_make_passing_evidence_eligible(
    changes, expected_reason
) -> None:
    baseline = _passing_evidence()
    assert decide_promotion(baseline).promote is True
    adversarial = replace(baseline, **changes)
    _assert_rejected_with(adversarial, expected_reason)
    assert decide_promotion(baseline).promote is True


@pytest.mark.parametrize(
    ("policy_changes", "reason"),
    [
        ({"minimum_games": 501}, "insufficient_games:"),
        ({"minimum_score_rate_lower_bound": 0.53}, "head_to_head_confidence_gate_failed:"),
        ({"minimum_scenario_pass_rate": 0.99}, "scenario_pass_rate_failed:"),
        ({"allow_critical_regressions": "false"}, "invalid_policy:allow_critical_regressions:not_boolean"),
    ],
)
def test_stricter_or_malformed_policy_cannot_bypass_evidence_gate(
    policy_changes, reason
) -> None:
    baseline = _passing_evidence()
    _assert_rejected_with(baseline, reason, replace(PromotionPolicy(), **policy_changes))
    assert decide_promotion(baseline).promote is True


def test_critical_frozen_regression_cannot_be_compensated_by_games_or_score() -> None:
    critical = Regression(
        scenario="last_defender_holdout",
        severity="CrItIcAl",
        details="frozen regression",
    )
    evidence = replace(
        _passing_evidence(),
        games_vs_champion=100_000,
        score_rate_vs_champion=1.0,
        score_rate_lower_bound=1.0,
        frozen_scenarios_passed=100,
        regressions=(critical,),
    )
    _assert_rejected_with(evidence, "critical_regressions:last_defender_holdout")
    assert evidence.regressions == (critical,)


def test_noncritical_regression_and_negative_goal_difference_are_diagnostic() -> None:
    # A negative goal difference alone is not a hidden veto in this policy.
    evidence = replace(
        _passing_evidence(),
        goal_difference_per_game=-3.0,
        regressions=(Regression("rotation", "warning"),),
    )
    assert decide_promotion(evidence).promote is True


def test_default_critical_regression_veto_can_only_be_disabled_explicitly() -> None:
    evidence = replace(
        _passing_evidence(),
        regressions=(Regression("corner_defence", "CRITICAL"),),
    )
    _assert_rejected_with(evidence, "critical_regressions:")
    assert decide_promotion(
        evidence, PromotionPolicy(allow_critical_regressions=True)
    ).promote is True


@pytest.mark.parametrize("games", [499, 500, 501])
@pytest.mark.parametrize("lower_bound", [0.49, 0.51, 0.52])
@pytest.mark.parametrize("scenarios_passed", [97, 98, 100])
@pytest.mark.parametrize("critical", [False, True])
@pytest.mark.parametrize("reproducible", [False, True])
def test_all_independent_default_gates_compose_with_logical_and(
    games, lower_bound, scenarios_passed, critical, reproducible
) -> None:
    # 108 valid combinations, including inclusive boundaries and mixed failures.
    regression = (Regression("frozen_defence", "critical"),) if critical else ()
    evidence = replace(
        _passing_evidence(),
        games_vs_champion=games,
        score_rate_lower_bound=lower_bound,
        frozen_scenarios_passed=scenarios_passed,
        regressions=regression,
        reproducible=reproducible,
    )
    expected = (
        games >= 500
        and lower_bound >= 0.51
        and scenarios_passed >= 98
        and not critical
        and reproducible
    )
    decision = decide_promotion(evidence)
    assert decision.promote is expected, (evidence, decision)
    assert bool(decision.reasons)
    assert decide_promotion(evidence) == decision
