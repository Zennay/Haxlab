from haxlab.evaluation.closed_loop_arena import (
    ClosedLoopArenaPolicy,
    decide_closed_loop_arena,
)


def _individual(
    *,
    far_stall: float = 0.05,
    held: float = 1.5,
    adapt: float = 0.7,
    samples: int = 4,
) -> dict:
    return {
        "samples": samples,
        "runtime_errors": 0,
        "boundary_rate": 0.0,
        "ood_rate": 0.02,
        "far_stall_rate": far_stall,
        "max_held_action_seconds": held,
        "context_adaptation_rate": adapt,
        "average_role_deviation": 120.0,
        "average_ball_distance": 200.0,
        "near_ball_rate": 0.2,
    }


def _role_pair(*, samples: int = 4) -> dict:
    candidate = _individual(samples=samples)
    reference = _individual(samples=samples)
    return {
        "candidate": candidate,
        "reference": reference,
        "delta": {
            "far_stall_rate": 0.0,
            "max_held_action_seconds": 0.0,
            "context_adaptation_rate": 0.0,
        },
    }


def _payload() -> dict:
    roles = ("gk", "dm", "am", "st")
    team_summary = {
        "matches": 8,
        "wins": 0,
        "draws": 8,
        "losses": 0,
        "proxy_match_score": 0.5,
        "candidate": {
            "formation_order_rate": 0.8,
            "collapsed_shape_rate": 0.1,
        },
        "reference": {
            "formation_order_rate": 0.8,
            "collapsed_shape_rate": 0.1,
        },
    }
    return {
        "schema": "haxlab-closed-loop-arena-v2",
        "evaluation_mode": (
            "paired_raw_policy_full_team_plus_plug_and_play_context_generalization_v2"
        ),
        "raw_policy_only": True,
        "safety_recovery_enabled": False,
        "paired_reference_design": True,
        "team_mode": {
            "summary": team_summary,
            "roles": {role: _role_pair(samples=8) for role in roles},
        },
        "plug_and_play": {
            "partner_model_count": 2,
            "summary": {
                "matches": 16,
                "wins": 0,
                "draws": 16,
                "losses": 0,
                "proxy_match_score": 0.5,
            },
            "by_role": {
                role: {
                    "team_outcome": {
                        "matches": 4,
                        "wins": 0,
                        "draws": 4,
                        "losses": 0,
                        "proxy_match_score": 0.5,
                        "candidate": {},
                        "reference": {},
                    },
                    "individual": _role_pair(),
                }
                for role in roles
            },
        },
    }


def test_initial_policy_is_fail_closed_until_calibrated() -> None:
    decision = decide_closed_loop_arena(_payload())

    assert decision.structurally_valid
    assert decision.behavior_gate_passed
    assert not decision.eligible_for_live_promotion
    assert "thresholds_not_calibrated" in decision.reasons


def test_identity_reference_passes_relative_checks() -> None:
    decision = decide_closed_loop_arena(_payload())

    assert decision.structurally_valid
    assert decision.behavior_gate_passed
    assert decision.checks["team_proxy_match_score"] == 0.5


def test_context_adaptation_regression_rejects_behavior_gate() -> None:
    payload = _payload()
    pair = payload["plug_and_play"]["by_role"]["am"]["individual"]
    pair["candidate"]["context_adaptation_rate"] = 0.2
    pair["reference"]["context_adaptation_rate"] = 0.7

    decision = decide_closed_loop_arena(payload)

    assert not decision.behavior_gate_passed
    assert any(
        reason.startswith("am:context_adaptation_rate")
        for reason in decision.reasons
    )


def test_safety_controller_invalidates_raw_policy_arena() -> None:
    payload = _payload()
    payload["safety_recovery_enabled"] = True

    decision = decide_closed_loop_arena(payload)

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "safety_recovery_must_be_disabled" in decision.reasons


def test_calibrated_policy_adds_absolute_strength_gate() -> None:
    payload = _payload()
    policy = ClosedLoopArenaPolicy(calibrated=True)

    decision = decide_closed_loop_arena(payload, policy=policy)

    assert decision.structurally_valid
    assert decision.behavior_gate_passed
    assert decision.eligible_for_live_promotion


def test_frozen_calibration_thresholds_are_evidence_backed() -> None:
    policy = ClosedLoopArenaPolicy()

    assert policy.policy_version == "closed-loop-arena-v2-frozen-1"
    assert policy.minimum_team_proxy_match_score == 0.50
    assert policy.minimum_plug_proxy_match_score == 0.50
    assert policy.max_absolute_far_stall_rate == 0.90
    assert policy.max_absolute_held_action_seconds == 30.0
    assert policy.minimum_absolute_context_adaptation_rate == 0.25


def test_frozen_absolute_context_gate_rejects_inert_policy() -> None:
    payload = _payload()
    for row in payload["plug_and_play"]["by_role"].values():
        row["individual"]["candidate"]["context_adaptation_rate"] = 0.0
        row["individual"]["reference"]["context_adaptation_rate"] = 0.0

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.behavior_gate_passed
    assert not decision.eligible_for_live_promotion
    assert any(
        "absolute_context_adaptation_rate" in reason
        for reason in decision.reasons
    )


def test_missing_candidate_metric_fails_closed_structurally() -> None:
    payload = _payload()
    del payload["plug_and_play"]["by_role"]["gk"]["individual"]["candidate"]["boundary_rate"]

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_metric:gk:candidate:boundary_rate:missing" in decision.reasons


def test_missing_context_adaptation_cannot_default_to_pass() -> None:
    payload = _payload()
    del payload["plug_and_play"]["by_role"]["am"]["individual"]["candidate"]["context_adaptation_rate"]

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:am:candidate:context_adaptation_rate:missing"
        in decision.reasons
    )


def test_non_finite_metric_fails_closed_structurally() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["st"]["individual"]["candidate"]["ood_rate"] = float("nan")

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_metric:st:candidate:ood_rate:non_finite" in decision.reasons


def test_runtime_error_count_must_be_numeric_integer() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["dm"]["individual"]["candidate"]["runtime_errors"] = "unknown"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:dm:candidate:runtime_errors:non_numeric"
        in decision.reasons
    )


def test_team_proxy_match_score_above_one_fails_closed() -> None:
    payload = _payload()
    payload["team_mode"]["summary"]["proxy_match_score"] = 1.25

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:team:proxy_match_score:above_maximum:1.250000>1.000000"
        in decision.reasons
    )


def test_negative_reference_rate_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["gk"]["individual"]["reference"][
        "boundary_rate"
    ] = -0.01

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:gk:reference:boundary_rate:below_minimum:-0.010000<0.000000"
        in decision.reasons
    )


def test_rate_above_one_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["am"]["individual"]["candidate"][
        "context_adaptation_rate"
    ] = 1.01

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:am:candidate:context_adaptation_rate:above_maximum:"
        "1.010000>1.000000"
        in decision.reasons
    )


def test_negative_duration_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["st"]["individual"]["candidate"][
        "max_held_action_seconds"
    ] = -0.5

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:st:candidate:max_held_action_seconds:"
        "below_minimum:-0.500000<0.000000"
        in decision.reasons
    )


def test_truthy_string_cannot_enable_raw_policy_contract() -> None:
    payload = _payload()
    payload["raw_policy_only"] = "true"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "arena_must_measure_raw_policy" in decision.reasons


def test_numeric_zero_cannot_disable_safety_recovery() -> None:
    payload = _payload()
    payload["safety_recovery_enabled"] = 0

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "safety_recovery_must_be_disabled" in decision.reasons


def test_numeric_one_cannot_claim_paired_reference_design() -> None:
    payload = _payload()
    payload["paired_reference_design"] = 1

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "paired_reference_design_required" in decision.reasons


def test_non_object_arena_payload_fails_closed_without_exception() -> None:
    decision = decide_closed_loop_arena(  # type: ignore[arg-type]
        ["not", "an", "object"],
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.behavior_gate_passed
    assert not decision.eligible_for_live_promotion
    assert decision.reasons == ("invalid_arena_payload",)


def test_non_object_team_mode_fails_closed_without_exception() -> None:
    payload = _payload()
    payload["team_mode"] = ["malformed"]

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_object:team_mode" in decision.reasons


def test_non_object_nested_individual_fails_closed_without_exception() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["dm"]["individual"] = "malformed"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_object:dm:individual" in decision.reasons

def test_team_match_tally_must_equal_declared_matches() -> None:
    payload = _payload()
    payload["team_mode"]["summary"]["wins"] = 1

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert "team:match_tally_mismatch:1+8+0!=8" in decision.reasons


def test_team_proxy_match_score_must_match_tally() -> None:
    payload = _payload()
    payload["team_mode"]["summary"]["proxy_match_score"] = 0.75

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert any(
        reason.startswith("team:proxy_match_score_mismatch:")
        for reason in decision.reasons
    )


def test_plug_summary_must_equal_sum_of_role_matches() -> None:
    payload = _payload()
    payload["plug_and_play"]["summary"].update({
        "matches": 20,
        "wins": 0,
        "draws": 20,
        "losses": 0,
        "proxy_match_score": 0.5,
    })

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert "plug:role_match_total_mismatch:16!=20" in decision.reasons


def test_role_outcome_tally_must_match_role_matches() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["st"]["team_outcome"]["losses"] = 1

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert "st:match_tally_mismatch:0+4+1!=4" in decision.reasons


def test_plug_role_sample_counts_must_match_role_outcomes() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["dm"]["individual"]["candidate"][
        "samples"
    ] = 3

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert "dm:candidate_samples_mismatch:3!=4" in decision.reasons


def test_team_role_sample_counts_must_match_team_outcomes() -> None:
    payload = _payload()
    payload["team_mode"]["roles"]["am"]["reference"]["samples"] = 7

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert "team:am:reference_samples_mismatch:7!=8" in decision.reasons



def test_stringified_team_score_fails_closed() -> None:
    payload = _payload()
    payload["team_mode"]["summary"]["proxy_match_score"] = "0.5"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:team:proxy_match_score:non_numeric"
        in decision.reasons
    )


def test_stringified_match_count_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["gk"]["team_outcome"][
        "matches"
    ] = "4"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_metric:gk:matches:non_numeric" in decision.reasons


def test_float_match_count_fails_closed_as_non_integer() -> None:
    payload = _payload()
    payload["team_mode"]["summary"]["matches"] = 8.0

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "invalid_metric:team:matches:not_integer" in decision.reasons


def test_stringified_partner_model_count_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["partner_model_count"] = "2"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:plug:partner_model_count:non_numeric"
        in decision.reasons
    )


def test_stringified_role_metric_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["st"]["individual"]["candidate"][
        "far_stall_rate"
    ] = "0.05"

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert (
        "invalid_metric:st:candidate:far_stall_rate:non_numeric"
        in decision.reasons
    )


def test_unexpected_string_role_fails_closed() -> None:
    payload = _payload()
    payload["plug_and_play"]["by_role"]["coach"] = {
        "team_outcome": {
            "matches": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "proxy_match_score": 0.0,
        },
        "individual": _role_pair(samples=0),
    }

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "plug_and_play:by_role:unexpected_role:'coach'" in decision.reasons


def test_unexpected_non_string_team_role_fails_closed() -> None:
    payload = _payload()
    payload["team_mode"]["roles"][99] = _role_pair(samples=8)

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "team_mode:roles:unexpected_role:99" in decision.reasons


def test_missing_evaluation_mode_fails_closed() -> None:
    payload = _payload()
    del payload["evaluation_mode"]

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "unsupported_evaluation_mode" in decision.reasons


def test_wrong_typed_evaluation_mode_fails_closed() -> None:
    payload = _payload()
    payload["evaluation_mode"] = 1

    decision = decide_closed_loop_arena(
        payload,
        policy=ClosedLoopArenaPolicy(calibrated=True),
    )

    assert not decision.structurally_valid
    assert not decision.eligible_for_live_promotion
    assert "unsupported_evaluation_mode" in decision.reasons

