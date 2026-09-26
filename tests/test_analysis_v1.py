from haxlab.analysis.v1 import summarize_replay_v1


def _payload() -> dict:
    return {
        "schemaVersion": 4,
        "featureVersion": "touch-chain-v1",
        "decoder": "node-haxball@2.3.1",
        "sourceFile": "sample.hbr2",
        "totalFrames": 3600,
        "simulation": {
            "sampleEveryTicks": 6,
            "sampledStateCount": 600,
            "teamGoals": {"red": 2, "blue": 1, "other": 0},
        },
        "ball": {
            "averageX": 5.0,
            "averageY": -2.0,
            "averageSpeed": 3.0,
            "heatmap": {"0:0": 10},
        },
        "featureSummary": {"touches": 30, "turnovers": 5},
        "players": [
            {
                "id": 1,
                "name": "Red A",
                "teamId": 1,
                "samples": 600,
                "averageX": -100.0,
                "averageY": 10.0,
                "heatmap": {"-5:0": 20},
                "inputEvents": 100,
                "kickEvents": 10,
                "touches": 12,
                "selfRetouches": 2,
                "teamTouchTransfersOut": 6,
                "turnovers": 2,
                "recoveries": 3,
                "kickTransfersToTeammate": 4,
                "kickTransfersToOpponent": 1,
                "touchGoals": 1,
                "touchAssists": 1,
                "underPressureTouchRate": 0.25,
                "pressuredRetentionRate": 0.5,
                "averageTouchProgression": 15.0,
                "averageProgression": 20.0,
            },
            {
                "id": 2,
                "name": "Blue A",
                "teamId": 2,
                "samples": 300,
                "averageX": 120.0,
                "averageY": -10.0,
                "heatmap": {"6:0": 12},
                "kickEvents": 5,
                "touches": 8,
                "selfRetouches": 1,
                "teamTouchTransfersOut": 2,
                "turnovers": 3,
                "recoveries": 1,
                "kickTransfersToTeammate": 1,
                "kickTransfersToOpponent": 2,
            },
        ],
    }


def test_replay_summary_preserves_explicit_semantics() -> None:
    summary = summarize_replay_v1(_payload())

    assert summary["schema"] == "haxlab-replay-analytics-v1"
    assert summary["match"]["team_goals"] == {"red": 2, "blue": 1}
    assert summary["players"][0]["actions"]["team_touch_transfers"] == 6
    assert summary["players"][0]["rates"]["kick_transfer_completion"] == 0.8
    assert summary["teams"]["red"]["actions"]["touch_goals"] == 1
    assert summary["teams"]["red"]["sample_weighted_mean_player_position"]["x"] == -100.0
    assert "shot attempts are not derivable reliably from touch-chain-v1" in summary["limitations"]


def test_unknown_decoder_schema_fails_closed() -> None:
    payload = _payload()
    payload["schemaVersion"] = 5

    try:
        summarize_replay_v1(payload)
    except ValueError as exc:
        assert "unsupported decoder schema" in str(exc)
    else:
        raise AssertionError("unsupported schema must fail closed")
