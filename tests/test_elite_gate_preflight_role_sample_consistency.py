from __future__ import annotations

import copy

from haxlab.evaluation.elite_gate_preflight import elite_gate_evidence_issues


def _complete_split() -> dict:
    role = {
        "samples": 100,
        "direction_accuracy": 0.72,
        "kick_f1": 0.2,
        "kick_true_rate": 0.01,
        "kick_predicted_rate": 0.01,
        "baselines": {"majority_direction_accuracy": 0.40},
    }
    return {
        "samples": 400,
        "direction_accuracy": 0.72,
        "kick_f1": 0.2,
        "kick_true_rate": 0.01,
        "kick_predicted_rate": 0.01,
        "baselines": {"majority_direction_accuracy": 0.40},
        "by_role": {
            "gk": copy.deepcopy(role),
            "dm": copy.deepcopy(role),
            "am": copy.deepcopy(role),
            "st": copy.deepcopy(role),
        },
    }


def _metadata(split: dict) -> dict:
    return {
        "training": {
            "frozen_holdout_used_for_selection": False,
            "kick_threshold_source": "validation_only",
        },
        "final_validation": _complete_split(),
        "final_holdout": split,
    }


def test_aggregate_samples_must_equal_role_sample_total() -> None:
    split = _complete_split()
    split["by_role"]["st"]["samples"] = 99

    issues = elite_gate_evidence_issues(_metadata(split))

    assert "final_holdout.samples_mismatch_by_role:400!=399" in issues


def test_consistent_role_sample_total_passes() -> None:
    issues = elite_gate_evidence_issues(_metadata(_complete_split()))

    assert not any(
        issue.startswith("final_holdout.samples_mismatch_by_role")
        for issue in issues
    )


def test_invalid_role_sample_does_not_emit_secondary_total_mismatch() -> None:
    split = _complete_split()
    split["by_role"]["st"]["samples"] = 0

    issues = elite_gate_evidence_issues(_metadata(split))

    assert "invalid_final_holdout.by_role.st.samples:below_minimum" in issues
    assert not any(
        issue.startswith("final_holdout.samples_mismatch_by_role")
        for issue in issues
    )
