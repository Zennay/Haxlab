from __future__ import annotations

import pytest

from haxlab.evaluation.duel_gate import decide_duel_gate
from haxlab.evaluation.replay_duel import build_replay_seeded_duel


def _benchmark(*, strength: float, scenario_sha: str = "a" * 64) -> dict:
    rows = []
    for scenario in range(1, 7):
        for side in (1, 2):
            rows.append({
                "scenario_index": scenario,
                "elite_team_id": side,
                "territory": {
                    "elite_half_rate": strength,
                    "elite_attack_third_rate": strength,
                },
                "progression": {"elite_share": strength},
            })
    return {
        "schema": "haxlab-elite-replay-scenario-benchmark-v1",
        "model_path": f"/tmp/model-{strength}.json",
        "scenario_path": "/tmp/scenarios.json",
        "scenario_sha256": scenario_sha,
        "territory": {
            "elite_half_rate": strength,
            "elite_attack_third_rate": strength,
        },
        "progression": {"elite_share": strength},
        "policy_activity": {
            "total_actions": 12000,
            "total_kicks": 12,
            "nonzero_movement_rate": 0.25,
            "runtime_errors": 0,
        },
        "by_elite_side": {
            "1": {
                "matches": 6,
                "elite_half_rate": strength,
                "elite_attack_third_rate": strength,
                "progression_share": strength,
                "nonzero_movement_rate": 0.25,
            },
            "2": {
                "matches": 6,
                "elite_half_rate": strength,
                "elite_attack_third_rate": strength,
                "progression_share": strength,
                "nonzero_movement_rate": 0.25,
            },
        },
        "match_results": rows,
    }


def test_replay_duel_is_compatible_with_existing_duel_gate() -> None:
    duel = build_replay_seeded_duel(
        _benchmark(strength=0.60),
        _benchmark(strength=0.40),
    )

    assert duel["schema"] == "haxlab-elite-replay-seeded-duel-v1"
    assert duel["evaluation_mode"] == "replay_seeded_proxy_v1"
    assert duel["scenario_sha256"] == "a" * 64
    assert (duel["wins"], duel["draws"], duel["losses"]) == (12, 0, 0)
    decision = decide_duel_gate(duel)
    assert decision.eligible_to_replace_champion is True


def test_replay_duel_rejects_mismatched_population() -> None:
    challenger = _benchmark(strength=0.60)
    champion = _benchmark(strength=0.40)
    champion["match_results"].pop()

    with pytest.raises(ValueError, match="populations do not match"):
        build_replay_seeded_duel(challenger, champion)


def test_replay_duel_rejects_mismatched_scenario_hashes() -> None:
    challenger = _benchmark(strength=0.60, scenario_sha="a" * 64)
    champion = _benchmark(strength=0.40, scenario_sha="b" * 64)

    with pytest.raises(ValueError, match="scenario hashes do not match"):
        build_replay_seeded_duel(challenger, champion)
