from haxlab.evaluation.elite_gate import (
    EliteLiveGatePolicy,
    decide_elite_live_gate,
)


def _metrics(**holdout_overrides):
    holdout = {
        "samples": 100_000,
        "direction_accuracy": 0.42,
        "kick_f1": 0.10,
        "kick_true_rate": 0.04,
        "kick_predicted_rate": 0.05,
        "baselines": {"majority_direction_accuracy": 0.18},
        "by_role": {
            role: {
                "direction_accuracy": 0.40,
                "baselines": {"majority_direction_accuracy": 0.18},
            }
            for role in ("gk", "dm", "am", "st")
        },
    }
    holdout.update(holdout_overrides)
    return {
        "training": {
            "frozen_holdout_used_for_selection": False,
            "kick_threshold_source": "validation_only",
            "kick_calibration": {"constraint_satisfied": True},
        },
        "final_validation": {"direction_accuracy": 0.43},
        "final_holdout": holdout,
    }


def test_good_elite_policy_is_eligible_for_live_test() -> None:
    decision = decide_elite_live_gate(_metrics())
    assert decision.eligible_for_live_test
    assert decision.checks["direction_lift"] > 0.20


def test_kick_spam_blocks_live_test() -> None:
    decision = decide_elite_live_gate(
        _metrics(kick_predicted_rate=0.12)
    )
    assert not decision.eligible_for_live_test
    assert any(reason.startswith("kick_rate_spam") for reason in decision.reasons)


def test_holdout_leakage_blocks_live_test() -> None:
    payload = _metrics()
    payload["training"]["frozen_holdout_used_for_selection"] = True
    decision = decide_elite_live_gate(payload)
    assert not decision.eligible_for_live_test
    assert "frozen_holdout_was_used_for_selection" in decision.reasons


def test_missing_role_blocks_live_test() -> None:
    payload = _metrics()
    del payload["final_holdout"]["by_role"]["gk"]
    decision = decide_elite_live_gate(payload)
    assert not decision.eligible_for_live_test
    assert "missing_role_metrics:gk" in decision.reasons


def test_role_kick_calibration_failure_blocks_live_test() -> None:
    payload = _metrics()
    payload["training"]["kick_calibration_by_role"] = {
        role: {"constraint_satisfied": role != "am"}
        for role in ("gk", "dm", "am", "st")
    }
    decision = decide_elite_live_gate(payload)
    assert not decision.eligible_for_live_test
    assert "role_kick_rate_constraint_failed:am" in decision.reasons
