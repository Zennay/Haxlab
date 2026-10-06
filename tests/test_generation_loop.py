import math

import pytest

from haxlab.research.generation_loop import (
    composite_score,
    evaluate_candidate,
    experiment_for_generation,
    mine_failure,
    metric_snapshot,
)


def test_generation_experiments_are_deterministic_and_bounded() -> None:
    first = experiment_for_generation(1)
    again = experiment_for_generation(1)
    later = experiment_for_generation(9)

    assert first == again
    assert first["seed"] != later["seed"]
    assert 8 <= first["hidden_dim"] <= 128
    assert 1 <= first["epochs"] <= 8
    assert first["learning_rate"] > 0
    assert first["l2"] >= 0


def test_rejected_generation_still_has_failure_inventory() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    candidate = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    decision = evaluate_candidate(candidate, champion)
    failure = mine_failure(7, candidate, champion, decision)

    assert decision["promote"] is False
    assert "insufficient_improvement" in " ".join(decision["reasons"])
    assert failure["next_action"] == "continue_with_next_bounded_experiment"
    assert failure["generation"] == 7


def test_promotion_requires_real_holdout_improvement() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000,
    }

    decision = evaluate_candidate(candidate, champion)

    assert composite_score(candidate) > composite_score(champion)
    assert decision["promote"] is True
    assert decision["improvement"] >= 0.001


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("joint_accuracy", "0.90", "non_numeric"),
        ("joint_accuracy", math.inf, "non_finite"),
        ("kick_f1", 1.01, "above_maximum"),
        ("direction_accuracy", -0.01, "below_minimum"),
        ("samples", 12.5, "not_integer"),
        ("samples", True, "non_numeric"),
    ],
)
def test_metric_snapshot_rejects_malformed_holdout_evidence(
    field: str,
    value: object,
    reason: str,
) -> None:
    holdout = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000,
    }
    holdout[field] = value
    with pytest.raises(ValueError, match=rf"invalid_final_holdout\.{field}:{reason}"):
        metric_snapshot({"final_holdout": holdout})


def test_evaluate_candidate_fails_closed_on_non_finite_candidate() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000.0,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": math.inf,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }

    decision = evaluate_candidate(candidate, champion)

    assert decision["promote"] is False
    assert decision["candidate_score"] is None
    assert "invalid_candidate.joint_accuracy:non_finite" in decision["reasons"]


def test_evaluate_candidate_fails_closed_on_corrupt_champion_snapshot() -> None:
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": "0.40",
        "samples": 1000.0,
    }

    decision = evaluate_candidate(candidate, champion)  # type: ignore[arg-type]

    assert decision["promote"] is False
    assert "invalid_champion.kick_f1:non_numeric" in decision["reasons"]


def test_evaluate_candidate_rejects_malformed_policy_threshold() -> None:
    champion = {
        "direction_accuracy": 0.70,
        "joint_accuracy": 0.55,
        "kick_f1": 0.40,
        "samples": 1000.0,
    }
    candidate = {
        "direction_accuracy": 0.71,
        "joint_accuracy": 0.57,
        "kick_f1": 0.43,
        "samples": 1000.0,
    }

    decision = evaluate_candidate(
        candidate,
        champion,
        maximum_kick_regression=2.0,
    )

    assert decision["promote"] is False
    assert "invalid_policy.maximum_kick_regression:above_maximum" in decision["reasons"]
