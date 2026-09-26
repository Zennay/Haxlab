from haxlab.evaluation.duel_gate import decide_duel_gate


def _good_duel() -> dict:
    return {
        "schema": "haxlab-elite-model-duel-v1",
        "matches": 12,
        "wins": 5,
        "draws": 4,
        "losses": 3,
        "territory": {
            "challenger_half_rate": 0.52,
            "champion_half_rate": 0.43,
            "challenger_attack_third_rate": 0.31,
            "champion_attack_third_rate": 0.28,
        },
        "challenger_runtime_errors": 0,
        "champion_runtime_errors": 0,
        "challenger_kick_action_rate": 0.05,
        "by_challenger_side": {
            "1": {"challenger_half_rate": 0.54},
            "2": {"challenger_half_rate": 0.50},
        },
        "goals": {
            "challenger": 7,
            "champion": 5,
            "differential": 2,
        },
    }


def test_good_challenger_can_replace_champion() -> None:
    decision = decide_duel_gate(_good_duel())
    assert decision.eligible_to_replace_champion
    assert decision.checks["match_score"] > 0.55


def test_equal_duel_does_not_replace_champion() -> None:
    duel = _good_duel()
    duel["wins"] = 0
    duel["draws"] = 12
    duel["losses"] = 0
    decision = decide_duel_gate(duel)
    assert not decision.eligible_to_replace_champion
    assert any(reason.startswith("match_score") for reason in decision.reasons)


def test_duel_side_collapse_blocks_replacement() -> None:
    duel = _good_duel()
    duel["by_challenger_side"]["1"]["challenger_half_rate"] = 0.60
    duel["by_challenger_side"]["2"]["challenger_half_rate"] = 0.10
    decision = decide_duel_gate(duel)
    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith("side_territory_gap")
        for reason in decision.reasons
    )


def test_duel_kick_spam_blocks_replacement() -> None:
    duel = _good_duel()
    duel["challenger_kick_action_rate"] = 0.20
    decision = decide_duel_gate(duel)
    assert not decision.eligible_to_replace_champion
    assert any(
        reason.startswith("challenger_kick_action_rate")
        for reason in decision.reasons
    )
