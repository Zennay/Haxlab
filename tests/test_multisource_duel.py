from __future__ import annotations

import pytest

from haxlab.evaluation.duel_gate import decide_duel_gate
from haxlab.evaluation.multisource_duel import aggregate_replay_seeded_duels


def _duel(index: int) -> dict:
    scenario_sha = f"{index:064x}"
    return {
        "schema": "haxlab-elite-replay-seeded-duel-v1",
        "evaluation_mode": "replay_seeded_proxy_v1",
        "challenger_model": "/models/challenger/runtime-model.json",
        "champion_model": "/models/champion/runtime-model.json",
        "scenario_sha256": scenario_sha,
        "matches": 32,
        "wins": 18,
        "draws": 8,
        "losses": 6,
        "territory": {
            "challenger_half_rate": 0.55,
            "champion_half_rate": 0.45,
            "challenger_attack_third_rate": 0.31,
            "champion_attack_third_rate": 0.27,
        },
        "challenger_runtime_errors": 0,
        "champion_runtime_errors": 0,
        "challenger_kick_action_rate": 0.002,
        "champion_kick_action_rate": 0.002,
        "challenger_nonzero_movement_rate": 0.28,
        "champion_nonzero_movement_rate": 0.20,
        "challenger_progression_share": 0.54,
        "champion_progression_share": 0.49,
        "by_challenger_side": {
            "1": {
                "matches": 16,
                "challenger_half_rate": 0.54,
                "challenger_attack_third_rate": 0.30,
                "challenger_progression_share": 0.53,
                "challenger_nonzero_movement_rate": 0.29,
            },
            "2": {
                "matches": 16,
                "challenger_half_rate": 0.56,
                "challenger_attack_third_rate": 0.32,
                "challenger_progression_share": 0.55,
                "challenger_nonzero_movement_rate": 0.27,
            },
        },
        "goals": {
            "challenger": 0,
            "champion": 0,
            "differential": 0,
        },
        "match_results": [],
    }


def _arena(count: int) -> dict:
    return {
        "schema": "haxlab-multisource-champion-arena-v2",
        "source_count": count,
        "sources": [
            {
                "slot": index,
                "files_sha256": {
                    "scenarios.json": f"{index:064x}",
                },
            }
            for index in range(1, count + 1)
        ],
    }


def test_multisource_duel_aggregates_equal_weight_sources() -> None:
    duels = [_duel(index) for index in range(1, 5)]
    result = aggregate_replay_seeded_duels(
        duels,
        arena_manifest=_arena(4),
        arena_manifest_sha256="a" * 64,
    )

    assert result["schema"] == "haxlab-elite-multisource-duel-v1"
    assert result["source_count"] == 4
    assert result["matches"] == 128
    assert result["wins"] == 72
    assert result["draws"] == 32
    assert result["losses"] == 24
    assert result["arena_manifest_sha256"] == "a" * 64
    assert result["challenger_nonzero_movement_rate"] == pytest.approx(0.28)
    assert result["challenger_progression_share"] == pytest.approx(0.54)

    decision = decide_duel_gate(result)
    assert decision.eligible_to_replace_champion
    assert decision.checks["source_count"] == 4
    assert decision.checks["match_score"] == pytest.approx(0.6875)


def test_multisource_duel_rejects_too_few_sources() -> None:
    result = aggregate_replay_seeded_duels(
        [_duel(1), _duel(2)],
        arena_manifest=_arena(2),
        arena_manifest_sha256="b" * 64,
    )

    decision = decide_duel_gate(result)

    assert not decision.eligible_to_replace_champion
    assert "insufficient_sources:2<3" in decision.reasons


def test_multisource_duel_validates_frozen_scenario_hash_order() -> None:
    arena = _arena(4)
    arena["sources"][2]["files_sha256"]["scenarios.json"] = "f" * 64

    with pytest.raises(ValueError, match="frozen arena order"):
        aggregate_replay_seeded_duels(
            [_duel(index) for index in range(1, 5)],
            arena_manifest=arena,
            arena_manifest_sha256="c" * 64,
        )


def test_multisource_replay_behavior_floor_still_applies() -> None:
    duels = [_duel(index) for index in range(1, 5)]
    for duel in duels:
        duel["challenger_nonzero_movement_rate"] = 0.05
        duel["challenger_progression_share"] = 0.20
        duel["challenger_kick_action_rate"] = 0.0

    result = aggregate_replay_seeded_duels(
        duels,
        arena_manifest=_arena(4),
        arena_manifest_sha256="d" * 64,
    )
    decision = decide_duel_gate(result)

    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith("challenger_progression_share")
        for reason in decision.reasons
    )
    assert any(
        reason.startswith("challenger_nonzero_movement_rate")
        for reason in decision.reasons
    )
    assert "challenger_no_kick_activity" in decision.reasons
