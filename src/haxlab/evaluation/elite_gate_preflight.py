from __future__ import annotations

from copy import deepcopy
from typing import Any


EXPECTED_ROLES = ("gk", "dm", "am", "st")
REQUIRED_SPLIT_FIELDS = (
    "samples",
    "direction_accuracy",
    "kick_f1",
    "kick_true_rate",
    "kick_predicted_rate",
)


def elite_gate_evidence_issues(
    metadata: dict[str, Any],
    *,
    split_key: str = "final_holdout",
) -> list[str]:
    """Return structural/provenance issues required by the frozen elite gate."""
    issues: list[str] = []
    training = metadata.get("training")
    if not isinstance(training, dict):
        issues.append("missing_training")
        training = {}

    if "frozen_holdout_used_for_selection" not in training:
        issues.append("missing_training.frozen_holdout_used_for_selection")
    elif training["frozen_holdout_used_for_selection"] is not False:
        issues.append("training.frozen_holdout_used_for_selection_must_be_false")

    if "kick_threshold_source" not in training:
        issues.append("missing_training.kick_threshold_source")
    elif str(training["kick_threshold_source"]) != "validation_only":
        issues.append("training.kick_threshold_source_must_be_validation_only")

    validation = metadata.get("final_validation")
    if not isinstance(validation, dict):
        issues.append("missing_final_validation")
    elif "direction_accuracy" not in validation:
        issues.append("missing_final_validation.direction_accuracy")

    split = metadata.get(split_key)
    if not isinstance(split, dict):
        issues.append(f"missing_{split_key}")
        return issues

    for field in REQUIRED_SPLIT_FIELDS:
        if field not in split:
            issues.append(f"missing_{split_key}.{field}")

    baselines = split.get("baselines")
    if not isinstance(baselines, dict):
        issues.append(f"missing_{split_key}.baselines")
    elif "majority_direction_accuracy" not in baselines:
        issues.append(
            f"missing_{split_key}.baselines.majority_direction_accuracy"
        )

    by_role = split.get("by_role")
    if not isinstance(by_role, dict):
        issues.append(f"missing_{split_key}.by_role")
        by_role = {}

    for role in EXPECTED_ROLES:
        row = by_role.get(role)
        if not isinstance(row, dict):
            issues.append(f"missing_{split_key}.by_role.{role}")
            continue
        if "direction_accuracy" not in row:
            issues.append(
                f"missing_{split_key}.by_role.{role}.direction_accuracy"
            )
        role_baselines = row.get("baselines")
        if not isinstance(role_baselines, dict):
            issues.append(f"missing_{split_key}.by_role.{role}.baselines")
        elif "majority_direction_accuracy" not in role_baselines:
            issues.append(
                f"missing_{split_key}.by_role.{role}.baselines."
                "majority_direction_accuracy"
            )

    return issues


def assert_elite_gate_evidence_schema(
    metadata: dict[str, Any],
    *,
    split_key: str = "final_holdout",
) -> None:
    issues = elite_gate_evidence_issues(metadata, split_key=split_key)
    if issues:
        raise ValueError(
            "elite gate evidence preflight failed: " + ", ".join(issues)
        )


def development_holdout_schema_proxy(
    *,
    training: dict[str, Any],
    validation_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Build a development-only schema proxy without reading sealed holdout."""
    return {
        "schema": "haxlab-elite-gate-development-preflight-v1",
        "schema_preflight_only": True,
        "training": deepcopy(training),
        "final_validation": deepcopy(validation_metrics),
        "final_holdout": deepcopy(validation_metrics),
    }


def assert_development_evaluator_gate_compatible(
    *,
    training: dict[str, Any],
    validation_metrics: dict[str, Any],
) -> dict[str, Any]:
    proxy = development_holdout_schema_proxy(
        training=training,
        validation_metrics=validation_metrics,
    )
    assert_elite_gate_evidence_schema(proxy)
    return proxy
