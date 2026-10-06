from haxlab.evaluation.multisource_duel import (
    build_multisource_duel,
    decide_multisource_duel_gate,
)


def _duel(seed: str, wins: int = 6, draws: int = 4, losses: int = 2) -> dict:
    return {
        "schema": "haxlab-elite-replay-seeded-duel-v1",
        "evaluation_mode": "replay_seeded_proxy_v1",
        "challenger_model": "/tmp/challenger/runtime-model.json",
        "champion_model": "/tmp/champion/runtime-model.json",
        "scenario_sha256": seed * 64,
        "matches": wins + draws + losses,
        "wins": wins,
        "draws": draws,
        "losses": losses,
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


def test_three_independent_sources_must_all_pass_existing_gate() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])

    decision = decide_multisource_duel_gate(payload)

    assert decision.eligible_to_replace_champion
    assert decision.checks["source_count"] == 3
    assert decision.checks["all_sources_passed"]


def test_one_failed_source_rejects_multisource_promotion() -> None:
    weak = _duel("b", wins=3, draws=6, losses=3)
    payload = build_multisource_duel([
        _duel("a"),
        weak,
        _duel("c"),
    ])

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert any(reason.startswith("source_2:match_score") for reason in decision.reasons)


def test_multisource_gate_requires_three_sources() -> None:
    payload = build_multisource_duel([_duel("a"), _duel("b")])

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "insufficient_sources:2<3" in decision.reasons


def test_duplicate_scenario_hash_is_rejected_during_build() -> None:
    try:
        build_multisource_duel([_duel("a"), _duel("a"), _duel("c")])
    except ValueError as exc:
        assert "duplicate scenario_sha256" in str(exc)
    else:
        raise AssertionError("duplicate scenario sources must fail closed")


def test_multisource_gate_rejects_wrapper_scenario_hash_mismatch() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["sources"][1]["scenario_sha256"] = "d" * 64

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "source_2:scenario_sha256_mismatch" in decision.reasons


def test_multisource_gate_rejects_invalid_wrapper_scenario_hash() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["sources"][2]["scenario_sha256"] = "not-a-sha"

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "source_3:invalid_scenario_sha256" in decision.reasons


def test_multisource_gate_rejects_model_provenance_mismatch() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["sources"][0]["duel"]["challenger_model"] = "/tmp/other/runtime-model.json"

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "source_1:challenger_model_mismatch" in decision.reasons


def test_multisource_gate_rejects_declared_source_count_mismatch() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["source_count"] = 4

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "source_count_mismatch:4!=3" in decision.reasons

def test_multisource_gate_rejects_tampered_aggregate_tallies() -> None:
    expected_reasons = {
        "matches": "aggregate_matches_mismatch:",
        "wins": "aggregate_wins_mismatch:",
        "draws": "aggregate_draws_mismatch:",
        "losses": "aggregate_losses_mismatch:",
    }
    for key, reason_prefix in expected_reasons.items():
        payload = build_multisource_duel([
            _duel("a"),
            _duel("b"),
            _duel("c"),
        ])
        payload[key] += 1

        decision = decide_multisource_duel_gate(payload)

        assert not decision.eligible_to_replace_champion
        assert any(
            reason.startswith(reason_prefix)
            for reason in decision.reasons
        )


def test_multisource_gate_rejects_tampered_aggregate_match_score() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["aggregate_match_score"] = 0.5

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith("aggregate_match_score_mismatch:")
        for reason in decision.reasons
    )


def test_multisource_gate_rejects_malformed_aggregate_types() -> None:
    payload = build_multisource_duel([
        _duel("a"),
        _duel("b"),
        _duel("c"),
    ])
    payload["matches"] = "36"
    payload["aggregate_match_score"] = "0.6666666667"

    decision = decide_multisource_duel_gate(payload)

    assert not decision.eligible_to_replace_champion
    assert "invalid_aggregate:matches:not_integer" in decision.reasons
    assert (
        "invalid_aggregate:aggregate_match_score:not_number"
        in decision.reasons
    )

