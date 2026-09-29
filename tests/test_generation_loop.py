from haxlab.research.generation_loop import (
    composite_score,
    evaluate_candidate,
    experiment_for_generation,
    mine_failure,
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
