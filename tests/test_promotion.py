from haxlab.evaluation.models import EvaluationEvidence, Regression
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
