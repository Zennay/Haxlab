from haxlab.coaching.replay_coach import analyze_replay, render_text


def _player(
    player_id: int,
    name: str,
    team: int,
    x: float,
    *,
    retained: int = 8,
    turnovers: int = 2,
    recoveries: int = 2,
    progression: float = 40.0,
) -> dict:
    return {
        "id": player_id,
        "name": name,
        "teamId": team,
        "samples": 6000,
        "averageX": x,
        "averageY": 0.0,
        "touches": retained + turnovers + 4,
        "selfRetouches": 2,
        "teamTouchTransfersOut": retained,
        "teamTouchReceipts": retained,
        "turnovers": turnovers,
        "recoveries": recoveries,
        "pressuredTransitions": 4,
        "retainedUnderPressure": 2,
        "touchProgressionEvents": 4,
        "touchProgressionSum": progression,
        "touchGoals": 0,
        "touchAssists": 0,
        "kickTransfersToTeammate": 4,
        "kickTransfersToOpponent": 1,
    }


def _payload() -> dict:
    players = [
        _player(1, "GK-R", 1, -160, turnovers=3),
        _player(2, "DM-R", 1, -30, recoveries=4),
        _player(3, "AM-R", 1, 45, progression=80),
        _player(4, "ST-R", 1, 145),
        _player(5, "GK-B", 2, 160),
        _player(6, "DM-B", 2, 50),
        _player(7, "AM-B", 2, -45),
        _player(8, "ST-B", 2, -145),
    ]
    touches = [
        # Red GK loses the ball in its own half three times.
        [100, 1, 1, -150, 0, 30, 1, 3, 6, -10],
        [400, 1, 1, -130, 0, 25, 1, 3, 6, -5],
        [700, 1, 1, -110, 0, 20, 1, 3, 7, -4],
        # Slow red recoveries: next red touch is > 180 frames away.
        [300, 6, 2, 0, 0, 30, 0, 2, 7, 20],
        [600, 7, 2, 10, 0, 30, 0, 2, 8, 20],
        [900, 8, 2, 20, 0, 30, 0, 3, 2, 10],
        # Red mostly bypasses its spine.
        [1000, 2, 1, -20, 0, 40, 0, 2, 4, 50],
        [1100, 4, 1, 80, 0, 30, 0, 2, 2, -20],
        [1200, 2, 1, -10, 0, 25, 0, 2, 4, 55],
        [1300, 4, 1, 100, 0, 20, 0, 2, 2, -25],
    ]
    return {
        "schemaVersion": 4,
        "featureVersion": "touch-chain-v1",
        "sourceFile": "test.hbr2",
        "totalFrames": 36000,
        "simulation": {
            "sampleEveryTicks": 6,
            "teamGoals": {"red": 0, "blue": 1, "other": 0},
        },
        "featureSummary": {"touches": 100, "turnovers": 20},
        "sparseEvents": {"touches": touches, "kicks": [], "goals": []},
        "players": players,
    }


def test_team_coach_detects_4v4_roles_and_high_risk_turnovers() -> None:
    report = analyze_replay(_payload(), [1])
    red = report["teams"][0]

    assert red["role_assignments"]["gk"]["name"] == "GK-R"
    assert red["role_assignments"]["dm"]["name"] == "DM-R"
    assert red["role_assignments"]["am"]["name"] == "AM-R"
    assert red["role_assignments"]["st"]["name"] == "ST-R"
    assert red["transition"]["own_half_turnovers"] == 3

    titles = {item["title"] for item in red["priorities"]}
    assert "Balverlies in eigen helft is een directe structurele dreiging" in titles
    assert any("GK" in title or "DM" in title for title in titles)


def test_intensive_text_report_contains_all_coaching_sections() -> None:
    text = render_text(analyze_replay(_payload(), [1]))

    assert "ROLE MAP & TEAM SHAPE" in text
    assert "BUILD-UP & CONNECTIVITY" in text
    assert "TRANSITIONS & RISK" in text
    assert "COACH PRIORITIES" in text
    assert "PLAYER-BY-PLAYER COACHING" in text
    assert "TRAINING PLAN" in text
    assert "GK — GK-R" in text
    assert "DM — DM-R" in text
    assert "AM — AM-R" in text
    assert "ST — ST-R" in text
