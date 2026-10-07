from haxlab.evaluation.models import (
    EvaluationEvidence,
    PromotionPolicy,
    Regression,
)
from haxlab.evaluation.promotion import decide_promotion


def _good_evidence(**overrides):
    values = dict(
        challenger_id="model-2",
        champion_id="model-1",
        games_vs_champion=1000,
        score_rate_vs_champion=0.55,
        score_rate_lower_bound=0.52,
        frozen_scenarios_total=100,
        frozen_scenarios_passed=100,
        regressions=(),
        reproducible=True,
    )
    values.update(overrides)
    return EvaluationEvidence(**values)


def test_good_challenger_can_be_promoted() -> None:
    assert decide_promotion(_good_evidence()).promote


def test_critical_regression_blocks_promotion() -> None:
    evidence = _good_evidence(
        regressions=(
            Regression(
                scenario="last_man_defence",
                severity="critical",
                details="large regression",
            ),
        )
    )

    decision = decide_promotion(evidence)

    assert not decision.promote
    assert any(reason.startswith("critical_regressions") for reason in decision.reasons)


def test_new_model_without_enough_games_is_rejected() -> None:
    decision = decide_promotion(_good_evidence(games_vs_champion=25))

    assert not decision.promote


def test_nan_confidence_bound_fails_closed() -> None:
    decision = decide_promotion(
        _good_evidence(score_rate_lower_bound=float("nan"))
    )

    assert not decision.promote
    assert (
        "invalid_evidence:score_rate_lower_bound:non_finite"
        in decision.reasons
    )


def test_passed_scenarios_cannot_exceed_total() -> None:
    decision = decide_promotion(
        _good_evidence(
            frozen_scenarios_total=100,
            frozen_scenarios_passed=101,
        )
    )

    assert not decision.promote
    assert (
        "invalid_evidence:frozen_scenarios_passed_exceeds_total:101>100"
        in decision.reasons
    )


def test_truthy_non_boolean_reproducible_does_not_pass() -> None:
    decision = decide_promotion(_good_evidence(reproducible="true"))

    assert not decision.promote
    assert "run_not_reproducible" in decision.reasons


def test_same_challenger_and_champion_is_rejected() -> None:
    decision = decide_promotion(
        _good_evidence(
            challenger_id="model-1",
            champion_id="model-1",
        )
    )

    assert not decision.promote
    assert "challenger_matches_champion" in decision.reasons


def test_confidence_bound_cannot_exceed_observed_score_rate() -> None:
    decision = decide_promotion(
        _good_evidence(
            score_rate_vs_champion=0.52,
            score_rate_lower_bound=0.53,
        )
    )

    assert not decision.promote
    assert (
        "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"
        in decision.reasons
    )


def test_non_integer_game_count_fails_closed() -> None:
    decision = decide_promotion(_good_evidence(games_vs_champion=1000.5))

    assert not decision.promote
    assert "invalid_evidence:games_vs_champion:not_integer" in decision.reasons


def test_nan_policy_confidence_threshold_fails_closed() -> None:
    decision = decide_promotion(
        _good_evidence(),
        PromotionPolicy(minimum_score_rate_lower_bound=float("nan")),
    )

    assert not decision.promote
    assert (
        "invalid_policy:minimum_score_rate_lower_bound:non_finite"
        in decision.reasons
    )


def test_string_policy_threshold_is_not_coerced() -> None:
    decision = decide_promotion(
        _good_evidence(),
        PromotionPolicy(minimum_scenario_pass_rate="0.50"),
    )

    assert not decision.promote
    assert (
        "invalid_policy:minimum_scenario_pass_rate:non_numeric"
        in decision.reasons
    )


def test_boolean_minimum_games_is_rejected() -> None:
    decision = decide_promotion(
        _good_evidence(),
        PromotionPolicy(minimum_games=True),
    )

    assert not decision.promote
    assert "invalid_policy:minimum_games:non_integer" in decision.reasons


def test_out_of_range_policy_rate_fails_closed() -> None:
    decision = decide_promotion(
        _good_evidence(),
        PromotionPolicy(minimum_scenario_pass_rate=1.01),
    )

    assert not decision.promote
    assert any(
        reason.startswith(
            "invalid_policy:minimum_scenario_pass_rate:above_maximum"
        )
        for reason in decision.reasons
    )


def test_truthy_string_cannot_allow_critical_regressions() -> None:
    evidence = _good_evidence(
        regressions=(
            Regression(
                scenario="kickoff",
                severity="critical",
                details="regressed",
            ),
        )
    )

    decision = decide_promotion(
        evidence,
        PromotionPolicy(allow_critical_regressions="false"),
    )

    assert not decision.promote
    assert (
        "invalid_policy:allow_critical_regressions:not_boolean"
        in decision.reasons
    )
    assert any(reason.startswith("critical_regressions") for reason in decision.reasons)


def test_non_policy_object_fails_closed() -> None:
    decision = decide_promotion(_good_evidence(), None)

    assert not decision.promote
    assert decision.reasons == ("invalid_policy:object_type",)


def test_non_tuple_regressions_fail_closed_without_exception() -> None:
    decision = decide_promotion(_good_evidence(regressions=None))

    assert not decision.promote
    assert "invalid_evidence:regressions:not_tuple" in decision.reasons


def test_non_regression_item_fails_closed_without_exception() -> None:
    decision = decide_promotion(
        _good_evidence(
            regressions=(
                {
                    "scenario": "kickoff",
                    "severity": "critical",
                    "details": "regressed",
                },
            )
        )
    )

    assert not decision.promote
    assert "invalid_evidence:regressions:0:object_type" in decision.reasons


def test_malformed_regression_fields_fail_closed() -> None:
    bad_severity = decide_promotion(
        _good_evidence(
            regressions=(
                Regression(
                    scenario="kickoff",
                    severity=1,
                    details="regressed",
                ),
            )
        )
    )
    bad_scenario = decide_promotion(
        _good_evidence(
            regressions=(
                Regression(
                    scenario="",
                    severity="critical",
                    details="regressed",
                ),
            )
        )
    )
    bad_details = decide_promotion(
        _good_evidence(
            regressions=(
                Regression(
                    scenario="kickoff",
                    severity="critical",
                    details=1,
                ),
            )
        )
    )

    assert not bad_severity.promote
    assert "invalid_evidence:regressions:0:severity:invalid" in bad_severity.reasons
    assert not bad_scenario.promote
    assert "invalid_evidence:regressions:0:scenario:invalid" in bad_scenario.reasons
    assert not bad_details.promote
    assert "invalid_evidence:regressions:0:details:not_string" in bad_details.reasons


def test_non_string_model_ids_fail_closed_without_coercion() -> None:
    bad_challenger = decide_promotion(_good_evidence(challenger_id=123))
    bad_champion = decide_promotion(_good_evidence(champion_id={"id": "model-1"}))

    assert not bad_challenger.promote
    assert "invalid_evidence:challenger_id" in bad_challenger.reasons
    assert not bad_champion.promote
    assert "invalid_evidence:champion_id" in bad_champion.reasons


def test_whitespace_critical_severity_still_blocks_promotion() -> None:
    decision = decide_promotion(
        _good_evidence(
            regressions=(
                Regression(
                    scenario="kickoff",
                    severity="  CrItIcAl  ",
                    details="regressed",
                ),
            )
        )
    )

    assert not decision.promote
    assert any(reason.startswith("critical_regressions") for reason in decision.reasons)
