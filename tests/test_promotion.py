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
