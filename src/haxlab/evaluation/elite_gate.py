from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EliteLiveGatePolicy:
    minimum_holdout_samples: int = 10_000
    minimum_direction_lift: float = 0.15
    minimum_role_direction_lift: float = 0.10
    minimum_kick_f1: float = 0.05
    maximum_kick_rate_multiplier: float = 1.5
    maximum_direction_generalization_drop: float = 0.08


@dataclass(frozen=True)
class EliteLiveGateDecision:
    eligible_for_live_test: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def _direction_lift(metrics: dict[str, Any]) -> float:
    return float(metrics.get("direction_accuracy", 0.0)) - float(
        (metrics.get("baselines") or {}).get("majority_direction_accuracy", 0.0)
    )


def decide_elite_live_gate(
    metadata: dict[str, Any],
    policy: EliteLiveGatePolicy = EliteLiveGatePolicy(),
) -> EliteLiveGateDecision:
    failures: list[str] = []
    checks: dict[str, Any] = {}

    training = metadata.get("training") or {}
    validation = metadata.get("final_validation") or {}
    holdout = metadata.get("final_holdout") or {}

    if bool(training.get("frozen_holdout_used_for_selection", True)):
        failures.append("frozen_holdout_was_used_for_selection")

    threshold_source = str(training.get("kick_threshold_source") or "")
    if threshold_source != "validation_only":
        failures.append(f"kick_threshold_not_validation_only:{threshold_source or 'missing'}")

    samples = int(holdout.get("samples") or 0)
    checks["holdout_samples"] = samples
    if samples < policy.minimum_holdout_samples:
        failures.append(
            f"insufficient_holdout_samples:{samples}<{policy.minimum_holdout_samples}"
        )

    overall_lift = _direction_lift(holdout)
    checks["direction_lift"] = overall_lift
    if overall_lift < policy.minimum_direction_lift:
        failures.append(
            "direction_lift_failed:"
            f"{overall_lift:.4f}<{policy.minimum_direction_lift:.4f}"
        )

    validation_accuracy = float(validation.get("direction_accuracy") or 0.0)
    holdout_accuracy = float(holdout.get("direction_accuracy") or 0.0)
    generalization_drop = max(0.0, validation_accuracy - holdout_accuracy)
    checks["direction_generalization_drop"] = generalization_drop
    if generalization_drop > policy.maximum_direction_generalization_drop:
        failures.append(
            "direction_generalization_drop:"
            f"{generalization_drop:.4f}>{policy.maximum_direction_generalization_drop:.4f}"
        )

    by_role = holdout.get("by_role") or {}
    expected_roles = ("gk", "dm", "am", "st")
    role_lifts: dict[str, float] = {}
    for role in expected_roles:
        role_metrics = by_role.get(role)
        if not role_metrics:
            failures.append(f"missing_role_metrics:{role}")
            continue
        lift = _direction_lift(role_metrics)
        role_lifts[role] = lift
        if lift < policy.minimum_role_direction_lift:
            failures.append(
                f"role_direction_lift_failed:{role}:{lift:.4f}"
                f"<{policy.minimum_role_direction_lift:.4f}"
            )
    checks["role_direction_lifts"] = role_lifts

    kick_f1 = float(holdout.get("kick_f1") or 0.0)
    checks["kick_f1"] = kick_f1
    if kick_f1 < policy.minimum_kick_f1:
        failures.append(
            f"kick_f1_failed:{kick_f1:.4f}<{policy.minimum_kick_f1:.4f}"
        )

    true_rate = float(holdout.get("kick_true_rate") or 0.0)
    predicted_rate = float(holdout.get("kick_predicted_rate") or 0.0)
    rate_cap = max(0.01, true_rate * policy.maximum_kick_rate_multiplier)
    checks["kick_true_rate"] = true_rate
    checks["kick_predicted_rate"] = predicted_rate
    checks["kick_predicted_rate_cap"] = rate_cap
    if predicted_rate > rate_cap:
        failures.append(
            "kick_rate_spam:"
            f"{predicted_rate:.4f}>{rate_cap:.4f}"
        )

    calibration = training.get("kick_calibration") or {}
    if calibration and not bool(calibration.get("constraint_satisfied", False)):
        failures.append("validation_kick_rate_constraint_failed")

    role_calibrations = training.get("kick_calibration_by_role") or {}
    role_calibration_checks: dict[str, bool] = {}
    for role in ("gk", "dm", "am", "st"):
        row = role_calibrations.get(role)
        if not row:
            # Backward-compatible with v0.1 models trained before per-role
            # calibration was introduced.
            continue
        passed = bool(row.get("constraint_satisfied", False))
        role_calibration_checks[role] = passed
        if not passed:
            failures.append(f"role_kick_rate_constraint_failed:{role}")
    checks["role_kick_calibration_constraints"] = role_calibration_checks

    if failures:
        return EliteLiveGateDecision(
            eligible_for_live_test=False,
            reasons=tuple(failures),
            checks=checks,
        )

    return EliteLiveGateDecision(
        eligible_for_live_test=True,
        reasons=(
            "frozen_holdout_integrity_passed",
            "direction_lift_passed",
            "all_role_lifts_passed",
            "kick_behavior_passed",
            "generalization_passed",
        ),
        checks=checks,
    )


def evaluate_metrics_file(
    metrics_path: Path,
    policy: EliteLiveGatePolicy = EliteLiveGatePolicy(),
) -> dict[str, Any]:
    metadata = json.loads(metrics_path.read_text(encoding="utf-8"))
    decision = decide_elite_live_gate(metadata, policy)
    return {
        "schema": "haxlab-elite-live-gate-v1",
        "metrics_path": str(metrics_path),
        "policy": asdict(policy),
        "eligible_for_live_test": decision.eligible_for_live_test,
        "reasons": list(decision.reasons),
        "checks": decision.checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-gate")
    parser.add_argument("metrics", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = evaluate_metrics_file(args.metrics)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["eligible_for_live_test"] else 2


if __name__ == "__main__":
    raise SystemExit(main())