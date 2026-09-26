from haxlab.learning.compare_elite import compare_metrics


def _metrics(direction: float, am_direction: float) -> dict:
    return {
        "training": {
            "best_epoch": 2,
            "state_jitter": None,
        },
        "final_holdout": {
            "direction_accuracy": direction,
            "joint_accuracy": direction - 0.01,
            "by_role": {
                "am": {
                    "direction_accuracy": am_direction,
                    "joint_accuracy": am_direction - 0.01,
                }
            },
        },
    }


def test_compare_metrics_reports_overall_and_role_deltas() -> None:
    baseline = _metrics(0.74, 0.73)
    challenger = _metrics(0.75, 0.76)
    challenger["training"]["state_jitter"] = {
        "std_normalized": 0.1,
        "roles": ["am", "st"],
    }

    result = compare_metrics(baseline, challenger)

    assert result["overall_holdout"]["direction_accuracy"]["delta"] == 0.01
    assert result["by_role_holdout"]["am"]["direction_accuracy"]["delta"] == 0.03
    assert result["challenger_training"]["state_jitter"]["std_normalized"] == 0.1
