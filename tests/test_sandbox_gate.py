from haxlab.evaluation.sandbox_gate import (
    SandboxGatePolicy,
    evaluate_sandbox_evidence,
)


def test_sandbox_gate_passes_balanced_arena_and_stable_policy() -> None:
    result = evaluate_sandbox_evidence(
        {
            "runtime_errors": 0,
            "non_loss_rate": 0.75,
            "progression": {"elite_share": 0.51},
            "policy_activity": {
                "nonzero_movement_rate": 0.65,
                "total_kicks": 12,
            },
        },
        {"aggregate_side_bias_abs": 0.08},
    )
    assert result["arena_valid"] is True
    assert result["eligible_for_champion_match"] is True


def test_sandbox_gate_rejects_biased_arena() -> None:
    result = evaluate_sandbox_evidence(
        {
            "runtime_errors": 0,
            "non_loss_rate": 1.0,
            "progression": {"elite_share": 0.80},
            "policy_activity": {
                "nonzero_movement_rate": 0.65,
                "total_kicks": 12,
            },
        },
        {"aggregate_side_bias_abs": 0.40},
    )
    assert result["arena_valid"] is False
    assert result["eligible_for_champion_match"] is False
    assert any(reason.startswith("arena_side_bias") for reason in result["reasons"])


def test_sandbox_gate_rejects_weak_progression() -> None:
    result = evaluate_sandbox_evidence(
        {
            "runtime_errors": 0,
            "non_loss_rate": 1.0,
            "progression": {"elite_share": 0.25},
            "policy_activity": {
                "nonzero_movement_rate": 0.65,
                "total_kicks": 12,
            },
        },
        {"aggregate_side_bias_abs": 0.05},
        SandboxGatePolicy(minimum_progression_share=0.42),
    )
    assert result["arena_valid"] is True
    assert result["eligible_for_champion_match"] is False
    assert any(reason.startswith("progression_share") for reason in result["reasons"])


def test_sandbox_gate_rejects_stationary_no_kick_policy() -> None:
    result = evaluate_sandbox_evidence(
        {
            "runtime_errors": 0,
            "non_loss_rate": 1.0,
            "progression": {"elite_share": 0.60},
            "policy_activity": {
                "nonzero_movement_rate": 0.0,
                "total_kicks": 0,
            },
        },
        {"aggregate_side_bias_abs": 0.01},
    )
    assert result["arena_valid"] is True
    assert result["eligible_for_champion_match"] is False
    assert any(
        reason.startswith("nonzero_movement_rate")
        for reason in result["reasons"]
    )
    assert any(reason.startswith("total_kicks") for reason in result["reasons"])
