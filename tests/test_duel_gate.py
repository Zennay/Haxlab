from haxlab.evaluation.duel_gate import DuelGatePolicy, decide_duel_gate


def _duel() -> dict:
    return {
        "schema": "haxlab-elite-replay-seeded-duel-v1",
        "evaluation_mode": "replay_seeded_proxy_v1",
        "challenger_model": "/tmp/challenger/runtime-model.json",
        "champion_model": "/tmp/champion/runtime-model.json",
        "scenario_sha256": "a" * 64,
        "matches": 12,
        "wins": 6,
        "draws": 4,
        "losses": 2,
        "territory": {
            "challenger_half_rate": 0.55,
            "champion_half_rate": 0.45,
            "challenger_attack_third_rate": 0.31,
            "champion_attack_third_rate": 0.27,
        },
        "challenger_runtime_errors": 0,
        "champion_runtime_errors": 0,
        "challenger_kick_action_rate": 0.01,
        "challenger_progression_share": 0.53,
        "challenger_nonzero_movement_rate": 0.25,
        "by_challenger_side": {
            "1": {"challenger_half_rate": 0.56},
            "2": {"challenger_half_rate": 0.54},
        },
        "goals": {"challenger": 0, "champion": 0, "differential": 0},
    }


def test_valid_replay_duel_still_passes() -> None:
    decision = decide_duel_gate(_duel())

    assert decision.eligible_to_replace_champion


def test_nan_kick_rate_fails_closed() -> None:
    payload = _duel()
    payload["challenger_kick_action_rate"] = float("nan")

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "invalid_metric:challenger_kick_action_rate:non_finite" in decision.reasons


def test_match_tally_must_equal_matches() -> None:
    payload = _duel()
    payload["losses"] = 99

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "match_tally_mismatch:6+4+99!=12" in decision.reasons


def test_missing_runtime_error_count_fails_closed() -> None:
    payload = _duel()
    del payload["challenger_runtime_errors"]

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "invalid_metric:challenger_runtime_errors:missing" in decision.reasons


def test_invalid_replay_scenario_hash_fails_closed() -> None:
    payload = _duel()
    payload["scenario_sha256"] = "not-a-sha"

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "invalid_scenario_sha256" in decision.reasons


def test_out_of_range_side_rate_fails_closed() -> None:
    payload = _duel()
    payload["by_challenger_side"]["2"]["challenger_half_rate"] = 1.5

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith("invalid_metric:side_2:challenger_half_rate:above_maximum")
        for reason in decision.reasons
    )


def test_non_numeric_match_count_does_not_raise() -> None:
    payload = _duel()
    payload["wins"] = "not-a-number"

    decision = decide_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "invalid_metric:wins:non_numeric" in decision.reasons



def test_nan_policy_match_threshold_fails_closed() -> None:
    decision = decide_duel_gate(
        _duel(),
        DuelGatePolicy(minimum_match_score=float("nan")),
    )

    assert not decision.eligible_to_replace_champion
    assert "invalid_policy:minimum_match_score:non_finite" in decision.reasons


def test_string_policy_rate_is_not_coerced() -> None:
    decision = decide_duel_gate(
        _duel(),
        DuelGatePolicy(maximum_kick_action_rate="0.10"),
    )

    assert not decision.eligible_to_replace_champion
    assert "invalid_policy:maximum_kick_action_rate:non_numeric" in decision.reasons


def test_boolean_policy_match_count_is_rejected() -> None:
    decision = decide_duel_gate(
        _duel(),
        DuelGatePolicy(minimum_matches=True),
    )

    assert not decision.eligible_to_replace_champion
    assert "invalid_policy:minimum_matches:non_integer" in decision.reasons


def test_nan_runtime_error_limit_cannot_fail_open() -> None:
    payload = _duel()
    payload["challenger_runtime_errors"] = 3

    decision = decide_duel_gate(
        payload,
        DuelGatePolicy(maximum_runtime_errors=float("nan")),
    )

    assert not decision.eligible_to_replace_champion
    assert "invalid_policy:maximum_runtime_errors:non_integer" in decision.reasons


def test_out_of_range_policy_rate_fails_closed() -> None:
    decision = decide_duel_gate(
        _duel(),
        DuelGatePolicy(maximum_side_territory_gap=1.01),
    )

    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith(
            "invalid_policy:maximum_side_territory_gap:above_maximum"
        )
        for reason in decision.reasons
    )


def test_non_boolean_kick_activity_policy_is_rejected() -> None:
    payload = _duel()
    payload["challenger_kick_action_rate"] = 0.0

    decision = decide_duel_gate(
        payload,
        DuelGatePolicy(require_replay_kick_activity="false"),
    )

    assert not decision.eligible_to_replace_champion
    assert (
        "invalid_policy:require_replay_kick_activity:not_boolean"
        in decision.reasons
    )


def test_non_policy_object_fails_closed() -> None:
    decision = decide_duel_gate(_duel(), None)

    assert not decision.eligible_to_replace_champion
    assert decision.reasons == ("invalid_policy:object_type",)


def test_non_object_duel_fails_closed() -> None:
    decision = decide_duel_gate(None)

    assert not decision.eligible_to_replace_champion
    assert decision.reasons == ("invalid_duel:object_type",)
