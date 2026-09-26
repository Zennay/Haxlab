from haxlab.evaluation.champion_gate import decide_champion_promotion
from haxlab.evaluation.sandbox_gate import decide_sandbox_gate


def _good_benchmark() -> dict:
    match_results = []
    for index in range(12):
        side = 1 if index % 2 == 0 else 2
        match_results.append(
            {
                "elite_team_id": side,
                "result": "draw",
                "goals": {"elite": 0, "baseline": 0},
                "territory": {
                    "elite_half_rate": 0.46,
                    "baseline_half_rate": 0.44,
                    "elite_attack_third_rate": 0.31,
                    "baseline_attack_third_rate": 0.29,
                },
                "policy": {
                    "runtime_errors": 0,
                    "total_actions": 1200,
                    "total_kicks": 60,
                },
            }
        )
    return {
        "schema": "haxlab-elite-sandbox-benchmark-v1",
        "matches": 12,
        "wins": 2,
        "draws": 8,
        "losses": 2,
        "non_loss_rate": 10 / 12,
        "runtime_errors": 0,
        "territory": {
            "elite_half_rate": 0.46,
            "baseline_half_rate": 0.44,
            "elite_attack_third_rate": 0.31,
            "baseline_attack_third_rate": 0.29,
        },
        "by_elite_side": {
            "1": {"elite_half_rate": 0.48},
            "2": {"elite_half_rate": 0.44},
        },
        "by_profile": {
            "balanced": {"matches": 4},
            "compact": {"matches": 4},
            "press": {"matches": 4},
        },
        "match_results": match_results,
    }


def _good_metrics() -> dict:
    return {
        "training": {
            "frozen_holdout_used_for_selection": False,
            "kick_threshold_source": "validation_only",
            "kick_calibration": {"constraint_satisfied": True},
        },
        "final_validation": {"direction_accuracy": 0.43},
        "final_holdout": {
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
        },
    }


def test_good_sandbox_is_promotion_eligible() -> None:
    decision = decide_sandbox_gate(_good_benchmark())
    assert decision.eligible_for_champion_promotion
    assert decision.checks["side_territory_gap"] < 0.10
    assert decision.checks["kick_action_rate"] == 0.05


def test_blue_side_collapse_blocks_promotion() -> None:
    benchmark = _good_benchmark()
    benchmark["by_elite_side"]["1"]["elite_half_rate"] = 0.42
    benchmark["by_elite_side"]["2"]["elite_half_rate"] = 0.0
    decision = decide_sandbox_gate(benchmark)
    assert not decision.eligible_for_champion_promotion
    assert any(
        reason.startswith("side_territory_gap")
        for reason in decision.reasons
    )


def test_sandbox_kick_spam_blocks_promotion() -> None:
    benchmark = _good_benchmark()
    for row in benchmark["match_results"]:
        row["policy"]["total_kicks"] = 300
    decision = decide_sandbox_gate(benchmark)
    assert not decision.eligible_for_champion_promotion
    assert any(
        reason.startswith("kick_action_rate")
        for reason in decision.reasons
    )


def test_runtime_errors_block_promotion() -> None:
    benchmark = _good_benchmark()
    benchmark["runtime_errors"] = 1
    decision = decide_sandbox_gate(benchmark)
    assert not decision.eligible_for_champion_promotion
    assert any(
        reason.startswith("runtime_errors")
        for reason in decision.reasons
    )


def test_champion_requires_offline_and_sandbox_pass() -> None:
    result = decide_champion_promotion(_good_metrics(), _good_benchmark())
    assert result["eligible_for_champion_promotion"]
    assert "offline_gate_passed" in result["reasons"]
    assert "sandbox_gate_passed" in result["reasons"]


def test_champion_fails_when_sandbox_side_bias_fails() -> None:
    benchmark = _good_benchmark()
    benchmark["by_elite_side"]["1"]["elite_half_rate"] = 0.50
    benchmark["by_elite_side"]["2"]["elite_half_rate"] = 0.0
    result = decide_champion_promotion(_good_metrics(), benchmark)
    assert not result["eligible_for_champion_promotion"]
    assert any(
        reason.startswith("sandbox:side_territory_gap")
        for reason in result["reasons"]
    )
