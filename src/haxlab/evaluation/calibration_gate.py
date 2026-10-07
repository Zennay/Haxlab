from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CALIBRATION_LABELS = ("champion-self", "candidate-d", "weak-zero")
EXPECTED_SOURCES = 3


@dataclass(frozen=True)
class CalibrationGateDecision:
    passed: bool
    reasons: tuple[str, ...]
    sanity: dict[str, Any]


def _strict_count(
    failures: list[str],
    row: dict[str, Any],
    key: str,
    label: str,
) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        failures.append(f"invalid_aggregate:{label}:{key}")
        return -1
    if value < 0:
        failures.append(f"invalid_aggregate:{label}:{key}:negative")
        return -1
    return value


def decide_calibration_gate(
    aggregate: dict[str, Any],
) -> CalibrationGateDecision:
    """Fail-closed frozen-policy calibration gate.

    Negative controls count as discrimination only when every source produced
    structurally valid evidence. A malformed evaluator must never satisfy the
    gate merely because malformed runs are not promotion-eligible.
    """
    failures: list[str] = []
    rows: dict[str, dict[str, Any]] = {}

    if not isinstance(aggregate, dict):
        return CalibrationGateDecision(
            passed=False,
            reasons=("invalid_calibration_aggregate",),
            sanity={
                "all_labels_have_three_sources": False,
                "all_runs_structurally_valid": False,
                "champion_identity_all_behavior_pass": False,
                "champion_identity_all_promotion_eligible": False,
                "negative_control_discriminated": False,
                "candidate_d_rejected": False,
                "candidate_d_behavior_passes": -1,
            },
        )

    for label in CALIBRATION_LABELS:
        row = aggregate.get(label)
        if not isinstance(row, dict):
            failures.append(f"missing_aggregate:{label}")
            rows[label] = {}
        else:
            rows[label] = row

    counts: dict[str, dict[str, int]] = {}
    structural: dict[str, bool] = {}
    for label, row in rows.items():
        counts[label] = {
            "sources": _strict_count(failures, row, "sources", label),
            "behavior_passes": _strict_count(
                failures, row, "behavior_passes", label
            ),
            "promotion_eligible_sources": _strict_count(
                failures, row, "promotion_eligible_sources", label
            ),
        }
        structural[label] = row.get("all_structurally_valid") is True
        if not structural[label]:
            failures.append(f"structural_evidence_invalid:{label}")

    all_three_sources = all(
        counts[label]["sources"] == EXPECTED_SOURCES
        for label in CALIBRATION_LABELS
    )
    if not all_three_sources:
        failures.append("calibration_source_count_mismatch")

    all_structural = all(structural.values())
    champion_behavior = (
        structural["champion-self"]
        and counts["champion-self"]["behavior_passes"] == EXPECTED_SOURCES
    )
    champion_eligible = (
        structural["champion-self"]
        and counts["champion-self"]["promotion_eligible_sources"]
        == EXPECTED_SOURCES
    )
    weak_discriminated = (
        structural["weak-zero"]
        and counts["weak-zero"]["promotion_eligible_sources"] == 0
    )
    candidate_rejected = (
        structural["candidate-d"]
        and counts["candidate-d"]["promotion_eligible_sources"] == 0
    )

    if not champion_behavior:
        failures.append("champion_identity_behavior_gate_failed")
    if not champion_eligible:
        failures.append("champion_identity_promotion_gate_failed")
    if not weak_discriminated:
        failures.append("negative_control_not_discriminated")
    if not candidate_rejected:
        failures.append("candidate_d_not_rejected")

    sanity = {
        "all_labels_have_three_sources": all_three_sources,
        "all_runs_structurally_valid": all_structural,
        "champion_identity_all_behavior_pass": champion_behavior,
        "champion_identity_all_promotion_eligible": champion_eligible,
        "negative_control_discriminated": weak_discriminated,
        "candidate_d_rejected": candidate_rejected,
        "candidate_d_behavior_passes": counts["candidate-d"]["behavior_passes"],
    }
    return CalibrationGateDecision(
        passed=not failures,
        reasons=tuple(dict.fromkeys(failures)),
        sanity=sanity,
    )
