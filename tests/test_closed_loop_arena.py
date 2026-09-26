from haxlab.evaluation.closed_loop_arena import (
    ClosedLoopArenaPolicy,
    decide_closed_loop_arena,
)


def _individual(
    *,
    far_stall: float = 0.05,
    held: float = 1.5,
    adapt: float = 0.7,
) -> dict:
    return {
        "samples": 4,
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


def _role_pair() -> dict:
    candidate = _individual()
    reference = _individual()
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
        "raw_policy_only": True,
        "safety_recovery_enabled": False,
        "paired_reference_design": True,
        "team_mode": {
            "summary": team_summary,
            "roles": {role: _role_pair() for role in roles},
        },
        "plug_and_play": {
            "partner_model_count": 2,
            "summary": {
                "matches": 16,
                "proxy_match_score": 0.5,
            },
            "by_role": {
                role: {
                    "team_outcome": {
                        "matches": 4,
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
    policy = ClosedLoopArenaPolicy(
        calibrated=True,
        minimum_team_proxy_match_score=0.5,
        minimum_plug_proxy_match_score=0.5,
        max_absolute_far_stall_rate=0.30,
        max_absolute_held_action_seconds=5.0,
        minimum_absolute_context_adaptation_rate=0.35,
    )

    decision = decide_closed_loop_arena(payload, policy=policy)

    assert decision.structurally_valid
    assert decision.behavior_gate_passed
    assert decision.eligible_for_live_promotion
