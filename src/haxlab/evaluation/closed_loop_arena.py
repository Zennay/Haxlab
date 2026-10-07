from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ARENA_SCHEMA = "haxlab-closed-loop-arena-v2"
ROLES = ("gk", "dm", "am", "st")


@dataclass(frozen=True)
class ClosedLoopArenaPolicy:
    policy_version: str = "closed-loop-arena-v2-frozen-1"
    calibrated: bool = False

    minimum_team_matches: int = 4
    minimum_plug_matches_per_role: int = 2
    minimum_partner_models: int = 1

    max_runtime_errors: int = 0

    # Relative-to-reference safety/stability tolerances. These are active
    # during calibration because they are identity-safe: champion vs itself
    # should pass them exactly.
    max_boundary_regression: float = 0.01
    max_ood_regression: float = 0.05
    max_far_stall_regression: float = 0.05
    max_role_deviation_regression: float = 50.0
    max_held_action_regression_seconds: float = 1.0
    max_context_adaptation_regression: float = 0.05
    max_formation_order_regression: float = 0.05
    max_shape_collapse_regression: float = 0.05

    # Frozen absolute thresholds calibrated on the 3-source x 16-scenario
    # Arena v2 batch at code SHA 46efb908a0e7. They remain fail-closed unless
    # calibrated=True / --calibrated is explicitly enabled by the caller.
    # Champion-self passed 3/3; Candidate D and the zero-policy control 0/3.
    minimum_team_proxy_match_score: float = 0.50
    minimum_plug_proxy_match_score: float = 0.50
    max_absolute_far_stall_rate: float = 0.90
    max_absolute_held_action_seconds: float = 30.0
    minimum_absolute_context_adaptation_rate: float = 0.25


@dataclass(frozen=True)
class ClosedLoopArenaDecision:
    structurally_valid: bool
    behavior_gate_passed: bool
    eligible_for_live_promotion: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def _policy_integer(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: int,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        failures.append(f"invalid_policy:{label}:not_integer")
        return
    if value < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:{value}<{minimum}"
        )


def _policy_number(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float | None = None,
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        failures.append(f"invalid_policy:{label}:non_numeric")
        return
    number = float(value)
    if not math.isfinite(number):
        failures.append(f"invalid_policy:{label}:non_finite")
        return
    if number < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:"
            f"{number:.6f}<{minimum:.6f}"
        )
    if maximum is not None and number > maximum:
        failures.append(
            f"invalid_policy:{label}:above_maximum:"
            f"{number:.6f}>{maximum:.6f}"
        )


def _validate_policy(policy: Any) -> tuple[str, ...]:
    if type(policy) is not ClosedLoopArenaPolicy:
        return ("invalid_policy:object_type",)

    failures: list[str] = []
    if (
        not isinstance(policy.policy_version, str)
        or not policy.policy_version.strip()
    ):
        failures.append("invalid_policy:policy_version:not_nonempty_string")
    if type(policy.calibrated) is not bool:
        failures.append("invalid_policy:calibrated:not_boolean")

    for label, value, minimum in (
        ("minimum_team_matches", policy.minimum_team_matches, 1),
        (
            "minimum_plug_matches_per_role",
            policy.minimum_plug_matches_per_role,
            1,
        ),
        ("minimum_partner_models", policy.minimum_partner_models, 1),
        ("max_runtime_errors", policy.max_runtime_errors, 0),
    ):
        _policy_integer(failures, value, label, minimum=minimum)

    for label, value in (
        ("max_boundary_regression", policy.max_boundary_regression),
        ("max_ood_regression", policy.max_ood_regression),
        ("max_far_stall_regression", policy.max_far_stall_regression),
        (
            "max_context_adaptation_regression",
            policy.max_context_adaptation_regression,
        ),
        (
            "max_formation_order_regression",
            policy.max_formation_order_regression,
        ),
        (
            "max_shape_collapse_regression",
            policy.max_shape_collapse_regression,
        ),
        (
            "minimum_team_proxy_match_score",
            policy.minimum_team_proxy_match_score,
        ),
        (
            "minimum_plug_proxy_match_score",
            policy.minimum_plug_proxy_match_score,
        ),
        (
            "max_absolute_far_stall_rate",
            policy.max_absolute_far_stall_rate,
        ),
        (
            "minimum_absolute_context_adaptation_rate",
            policy.minimum_absolute_context_adaptation_rate,
        ),
    ):
        _policy_number(
            failures,
            value,
            label,
            minimum=0.0,
            maximum=1.0,
        )

    for label, value in (
        (
            "max_role_deviation_regression",
            policy.max_role_deviation_regression,
        ),
        (
            "max_held_action_regression_seconds",
            policy.max_held_action_regression_seconds,
        ),
        (
            "max_absolute_held_action_seconds",
            policy.max_absolute_held_action_seconds,
        ),
    ):
        _policy_number(failures, value, label, minimum=0.0)

    return tuple(failures)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _require_numeric_field(
    failures: list[str],
    *,
    mapping: dict[str, Any],
    key: str,
    label: str,
    integer: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
) -> None:
    if key not in mapping:
        failures.append(f"invalid_metric:{label}:missing")
        return

    value = mapping.get(key)
    if isinstance(value, bool):
        failures.append(f"invalid_metric:{label}:boolean")
        return
    if not isinstance(value, (int, float)):
        failures.append(f"invalid_metric:{label}:non_numeric")
        return
    if integer and not isinstance(value, int):
        failures.append(f"invalid_metric:{label}:not_integer")
        return

    number = float(value)
    if not math.isfinite(number):
        failures.append(f"invalid_metric:{label}:non_finite")
        return
    if minimum is not None and number < minimum:
        failures.append(
            f"invalid_metric:{label}:below_minimum:{number:.6f}<{minimum:.6f}"
        )
    if maximum is not None and number > maximum:
        failures.append(
            f"invalid_metric:{label}:above_maximum:{number:.6f}>{maximum:.6f}"
        )


def _require_mapping(
    failures: list[str],
    value: Any,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        failures.append(f"invalid_object:{label}")
        return {}
    return value


def _validate_outcome_summary(
    failures: list[str],
    *,
    mapping: dict[str, Any],
    label: str,
) -> tuple[int, int, int, int, float]:
    for key in ("matches", "wins", "draws", "losses"):
        _require_numeric_field(
            failures,
            mapping=mapping,
            key=key,
            label=f"{label}:{key}",
            integer=True,
            minimum=0,
        )
    _require_numeric_field(
        failures,
        mapping=mapping,
        key="proxy_match_score",
        label=f"{label}:proxy_match_score",
        minimum=0.0,
        maximum=1.0,
    )

    matches = _integer(mapping.get("matches"))
    wins = _integer(mapping.get("wins"))
    draws = _integer(mapping.get("draws"))
    losses = _integer(mapping.get("losses"))
    proxy_match_score = _number(mapping.get("proxy_match_score"))

    if wins + draws + losses != matches:
        failures.append(
            f"{label}:match_tally_mismatch:"
            f"{wins}+{draws}+{losses}!={matches}"
        )

    expected_proxy_match_score = (
        (wins + 0.5 * draws) / matches if matches > 0 else 0.0
    )
    if not math.isclose(
        proxy_match_score,
        expected_proxy_match_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        failures.append(
            f"{label}:proxy_match_score_mismatch:"
            f"{proxy_match_score:.12f}!={expected_proxy_match_score:.12f}"
        )

    return matches, wins, draws, losses, proxy_match_score


def _relative_max_check(
    failures: list[str],
    *,
    label: str,
    candidate: float,
    reference: float,
    tolerance: float,
) -> None:
    if candidate > reference + tolerance:
        failures.append(
            f"{label}:{candidate:.6f}>reference:"
            f"{reference:.6f}+{tolerance:.6f}"
        )


def _relative_min_check(
    failures: list[str],
    *,
    label: str,
    candidate: float,
    reference: float,
    tolerance: float,
) -> None:
    if candidate < reference - tolerance:
        failures.append(
            f"{label}:{candidate:.6f}<reference:"
            f"{reference:.6f}-{tolerance:.6f}"
        )


def decide_closed_loop_arena(
    payload: dict[str, Any],
    *,
    policy: ClosedLoopArenaPolicy = ClosedLoopArenaPolicy(),
) -> ClosedLoopArenaDecision:
    policy_failures = _validate_policy(policy)
    if policy_failures:
        return ClosedLoopArenaDecision(
            structurally_valid=False,
            behavior_gate_passed=False,
            eligible_for_live_promotion=False,
            reasons=policy_failures,
            checks={},
        )

    structural_failures: list[str] = []
    behavior_failures: list[str] = []
    if not isinstance(payload, dict):
        return ClosedLoopArenaDecision(
            structurally_valid=False,
            behavior_gate_passed=False,
            eligible_for_live_promotion=False,
            reasons=("invalid_arena_payload",),
            checks={
                "policy_version": policy.policy_version,
                "policy_calibrated": policy.calibrated,
            },
        )

    checks: dict[str, Any] = {
        "policy_version": policy.policy_version,
        "policy_calibrated": policy.calibrated,
        "schema": payload.get("schema"),
        "raw_policy_only": payload.get("raw_policy_only"),
        "safety_recovery_enabled": payload.get("safety_recovery_enabled"),
        "paired_reference_design": payload.get("paired_reference_design"),
    }

    if payload.get("schema") != ARENA_SCHEMA:
        structural_failures.append(
            f"unsupported_schema:{payload.get('schema') or 'missing'}"
        )
    if payload.get("raw_policy_only") is not True:
        structural_failures.append("arena_must_measure_raw_policy")
    if payload.get("safety_recovery_enabled") is not False:
        structural_failures.append("safety_recovery_must_be_disabled")
    if payload.get("paired_reference_design") is not True:
        structural_failures.append("paired_reference_design_required")

    team_mode = _require_mapping(
        structural_failures,
        payload.get("team_mode"),
        "team_mode",
    )
    team_summary = _require_mapping(
        structural_failures,
        team_mode.get("summary"),
        "team_mode:summary",
    )
    team_roles = _require_mapping(
        structural_failures,
        team_mode.get("roles"),
        "team_mode:roles",
    )
    (
        team_matches,
        team_wins,
        team_draws,
        team_losses,
        team_proxy_match_score,
    ) = _validate_outcome_summary(
        structural_failures,
        mapping=team_summary,
        label="team",
    )
    checks["team_matches"] = team_matches
    checks["team_wins"] = team_wins
    checks["team_draws"] = team_draws
    checks["team_losses"] = team_losses
    checks["team_proxy_match_score"] = team_proxy_match_score
    if team_matches < policy.minimum_team_matches:
        structural_failures.append(
            f"insufficient_team_matches:{team_matches}<"
            f"{policy.minimum_team_matches}"
        )

    candidate_team = _require_mapping(
        structural_failures,
        team_summary.get("candidate"),
        "team:candidate",
    )
    reference_team = _require_mapping(
        structural_failures,
        team_summary.get("reference"),
        "team:reference",
    )
    for side, metrics in (
        ("candidate", candidate_team),
        ("reference", reference_team),
    ):
        for key in ("formation_order_rate", "collapsed_shape_rate"):
            _require_numeric_field(
                structural_failures,
                mapping=metrics,
                key=key,
                label=f"team:{side}:{key}",
                minimum=0.0,
                maximum=1.0,
            )
    _relative_min_check(
        behavior_failures,
        label="team:formation_order_rate",
        candidate=_number(candidate_team.get("formation_order_rate")),
        reference=_number(reference_team.get("formation_order_rate")),
        tolerance=policy.max_formation_order_regression,
    )
    _relative_max_check(
        behavior_failures,
        label="team:collapsed_shape_rate",
        candidate=_number(candidate_team.get("collapsed_shape_rate")),
        reference=_number(reference_team.get("collapsed_shape_rate")),
        tolerance=policy.max_shape_collapse_regression,
    )

    plug = _require_mapping(
        structural_failures,
        payload.get("plug_and_play"),
        "plug_and_play",
    )
    _require_numeric_field(
        structural_failures,
        mapping=plug,
        key="partner_model_count",
        label="plug:partner_model_count",
        integer=True,
        minimum=0,
    )
    plug_summary = _require_mapping(
        structural_failures,
        plug.get("summary"),
        "plug_and_play:summary",
    )
    (
        plug_matches,
        plug_wins,
        plug_draws,
        plug_losses,
        plug_proxy_match_score,
    ) = _validate_outcome_summary(
        structural_failures,
        mapping=plug_summary,
        label="plug",
    )
    partner_model_count = _integer(plug.get("partner_model_count"))
    checks["partner_model_count"] = partner_model_count
    checks["plug_matches"] = plug_matches
    checks["plug_wins"] = plug_wins
    checks["plug_draws"] = plug_draws
    checks["plug_losses"] = plug_losses
    checks["plug_proxy_match_score"] = plug_proxy_match_score
    if partner_model_count < policy.minimum_partner_models:
        structural_failures.append(
            f"insufficient_partner_models:{partner_model_count}<"
            f"{policy.minimum_partner_models}"
        )

    by_role = _require_mapping(
        structural_failures,
        plug.get("by_role"),
        "plug_and_play:by_role",
    )
    role_checks: dict[str, Any] = {}
    for role in ROLES:
        role_row = _require_mapping(
            structural_failures,
            by_role.get(role),
            f"plug_and_play:by_role:{role}",
        )
        outcome = _require_mapping(
            structural_failures,
            role_row.get("team_outcome"),
            f"{role}:team_outcome",
        )
        individual = _require_mapping(
            structural_failures,
            role_row.get("individual"),
            f"{role}:individual",
        )
        candidate = _require_mapping(
            structural_failures,
            individual.get("candidate"),
            f"{role}:individual:candidate",
        )
        reference = _require_mapping(
            structural_failures,
            individual.get("reference"),
            f"{role}:individual:reference",
        )

        (
            matches,
            wins,
            draws,
            losses,
            role_proxy_score,
        ) = _validate_outcome_summary(
            structural_failures,
            mapping=outcome,
            label=role,
        )
        _require_numeric_field(
            structural_failures,
            mapping=candidate,
            key="runtime_errors",
            label=f"{role}:candidate:runtime_errors",
            integer=True,
            minimum=0,
        )
        for side, metrics in (
            ("candidate", candidate),
            ("reference", reference),
        ):
            _require_numeric_field(
                structural_failures,
                mapping=metrics,
                key="samples",
                label=f"{role}:{side}:samples",
                integer=True,
                minimum=0,
            )
        for side, metrics in (
            ("candidate", candidate),
            ("reference", reference),
        ):
            for key in (
                "boundary_rate",
                "ood_rate",
                "far_stall_rate",
                "context_adaptation_rate",
            ):
                _require_numeric_field(
                    structural_failures,
                    mapping=metrics,
                    key=key,
                    label=f"{role}:{side}:{key}",
                    minimum=0.0,
                    maximum=1.0,
                )
            for key in (
                "max_held_action_seconds",
                "average_role_deviation",
            ):
                _require_numeric_field(
                    structural_failures,
                    mapping=metrics,
                    key=key,
                    label=f"{role}:{side}:{key}",
                    minimum=0.0,
                )

        runtime_errors = _integer(candidate.get("runtime_errors"))
        candidate_samples = _integer(candidate.get("samples"))
        reference_samples = _integer(reference.get("samples"))
        candidate_boundary = _number(candidate.get("boundary_rate"))
        reference_boundary = _number(reference.get("boundary_rate"))
        candidate_ood = _number(candidate.get("ood_rate"))
        reference_ood = _number(reference.get("ood_rate"))
        candidate_stall = _number(candidate.get("far_stall_rate"))
        reference_stall = _number(reference.get("far_stall_rate"))
        candidate_held = _number(candidate.get("max_held_action_seconds"))
        reference_held = _number(reference.get("max_held_action_seconds"))
        candidate_adapt = _number(
            candidate.get("context_adaptation_rate"),
            default=1.0,
        )
        reference_adapt = _number(
            reference.get("context_adaptation_rate"),
            default=1.0,
        )
        candidate_role_deviation = _number(
            candidate.get("average_role_deviation")
        )
        reference_role_deviation = _number(
            reference.get("average_role_deviation")
        )

        role_checks[role] = {
            "matches": matches,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "proxy_match_score": role_proxy_score,
            "candidate": candidate,
            "reference": reference,
        }

        if candidate_samples != matches:
            structural_failures.append(
                f"{role}:candidate_samples_mismatch:"
                f"{candidate_samples}!={matches}"
            )
        if reference_samples != matches:
            structural_failures.append(
                f"{role}:reference_samples_mismatch:"
                f"{reference_samples}!={matches}"
            )

        if matches < policy.minimum_plug_matches_per_role:
            structural_failures.append(
                f"{role}:insufficient_plug_matches:{matches}<"
                f"{policy.minimum_plug_matches_per_role}"
            )
        if runtime_errors > policy.max_runtime_errors:
            behavior_failures.append(
                f"{role}:runtime_errors:{runtime_errors}>"
                f"{policy.max_runtime_errors}"
            )

        _relative_max_check(
            behavior_failures,
            label=f"{role}:boundary_rate",
            candidate=candidate_boundary,
            reference=reference_boundary,
            tolerance=policy.max_boundary_regression,
        )
        _relative_max_check(
            behavior_failures,
            label=f"{role}:ood_rate",
            candidate=candidate_ood,
            reference=reference_ood,
            tolerance=policy.max_ood_regression,
        )
        _relative_max_check(
            behavior_failures,
            label=f"{role}:far_stall_rate",
            candidate=candidate_stall,
            reference=reference_stall,
            tolerance=policy.max_far_stall_regression,
        )
        _relative_max_check(
            behavior_failures,
            label=f"{role}:average_role_deviation",
            candidate=candidate_role_deviation,
            reference=reference_role_deviation,
            tolerance=policy.max_role_deviation_regression,
        )
        _relative_max_check(
            behavior_failures,
            label=f"{role}:max_held_action_seconds",
            candidate=candidate_held,
            reference=reference_held,
            tolerance=policy.max_held_action_regression_seconds,
        )
        _relative_min_check(
            behavior_failures,
            label=f"{role}:context_adaptation_rate",
            candidate=candidate_adapt,
            reference=reference_adapt,
            tolerance=policy.max_context_adaptation_regression,
        )

        if policy.calibrated:
            if role_proxy_score < policy.minimum_plug_proxy_match_score:
                behavior_failures.append(
                    f"{role}:plug_proxy_match_score:"
                    f"{role_proxy_score:.6f}<"
                    f"{policy.minimum_plug_proxy_match_score:.6f}"
                )
            if candidate_stall > policy.max_absolute_far_stall_rate:
                behavior_failures.append(
                    f"{role}:absolute_far_stall_rate:"
                    f"{candidate_stall:.6f}>"
                    f"{policy.max_absolute_far_stall_rate:.6f}"
                )
            if candidate_held > policy.max_absolute_held_action_seconds:
                behavior_failures.append(
                    f"{role}:absolute_max_held_action_seconds:"
                    f"{candidate_held:.6f}>"
                    f"{policy.max_absolute_held_action_seconds:.6f}"
                )
            if (
                candidate_adapt
                < policy.minimum_absolute_context_adaptation_rate
            ):
                behavior_failures.append(
                    f"{role}:absolute_context_adaptation_rate:"
                    f"{candidate_adapt:.6f}<"
                    f"{policy.minimum_absolute_context_adaptation_rate:.6f}"
                )

    checks["roles"] = role_checks

    total_plug_role_matches = sum(
        int(row.get("matches", 0))
        for row in role_checks.values()
    )
    checks["plug_role_matches_total"] = total_plug_role_matches
    if total_plug_role_matches != plug_matches:
        structural_failures.append(
            "plug:role_match_total_mismatch:"
            f"{total_plug_role_matches}!={plug_matches}"
        )

    team_role_checks: dict[str, Any] = {}
    for role in ROLES:
        if role not in team_roles:
            structural_failures.append(f"team_mode_missing_role:{role}")
        if role not in by_role:
            structural_failures.append(f"plug_and_play_missing_role:{role}")

        team_role = _require_mapping(
            structural_failures,
            team_roles.get(role),
            f"team_mode:roles:{role}",
        )
        team_candidate = _require_mapping(
            structural_failures,
            team_role.get("candidate"),
            f"team_mode:{role}:candidate",
        )
        team_reference = _require_mapping(
            structural_failures,
            team_role.get("reference"),
            f"team_mode:{role}:reference",
        )
        for side, metrics in (
            ("candidate", team_candidate),
            ("reference", team_reference),
        ):
            _require_numeric_field(
                structural_failures,
                mapping=metrics,
                key="samples",
                label=f"team:{role}:{side}:samples",
                integer=True,
                minimum=0,
            )
        candidate_samples = _integer(team_candidate.get("samples"))
        reference_samples = _integer(team_reference.get("samples"))
        if candidate_samples != team_matches:
            structural_failures.append(
                f"team:{role}:candidate_samples_mismatch:"
                f"{candidate_samples}!={team_matches}"
            )
        if reference_samples != team_matches:
            structural_failures.append(
                f"team:{role}:reference_samples_mismatch:"
                f"{reference_samples}!={team_matches}"
            )
        team_role_checks[role] = {
            "candidate_samples": candidate_samples,
            "reference_samples": reference_samples,
        }
    checks["team_roles"] = team_role_checks

    if policy.calibrated:
        team_proxy_score = team_proxy_match_score
        if team_proxy_score < policy.minimum_team_proxy_match_score:
            behavior_failures.append(
                "team:proxy_match_score:"
                f"{team_proxy_score:.6f}<"
                f"{policy.minimum_team_proxy_match_score:.6f}"
            )

    structurally_valid = not structural_failures
    behavior_gate_passed = structurally_valid and not behavior_failures

    reasons = [*structural_failures, *behavior_failures]
    if not policy.calibrated:
        reasons.append("thresholds_not_calibrated")

    eligible = (
        structurally_valid
        and behavior_gate_passed
        and policy.calibrated
    )

    if not reasons:
        reasons = ["closed_loop_arena_v2_passed"]

    checks["structural_failures"] = structural_failures
    checks["behavior_failures"] = behavior_failures

    return ClosedLoopArenaDecision(
        structurally_valid=structurally_valid,
        behavior_gate_passed=behavior_gate_passed,
        eligible_for_live_promotion=eligible,
        reasons=tuple(reasons),
        checks=checks,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-closed-loop-arena")
    parser.add_argument("arena", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--calibrated",
        action="store_true",
        help=(
            "Enable absolute promotion thresholds only after calibration "
            "has been frozen."
        ),
    )
    args = parser.parse_args()

    payload = json.loads(args.arena.read_text(encoding="utf-8"))
    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=args.calibrated),
    )
    result = {
        "schema": "haxlab-closed-loop-arena-decision-v2",
        "structurally_valid": decision.structurally_valid,
        "behavior_gate_passed": decision.behavior_gate_passed,
        "eligible_for_live_promotion": (
            decision.eligible_for_live_promotion
        ),
        "reasons": list(decision.reasons),
        "checks": decision.checks,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
