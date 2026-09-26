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


def test_batch_builder_records_provenance(tmp_path) -> None:
    import hashlib
    import json

    from haxlab.analysis.batch import build_analytics_batch

    source_root = tmp_path / "state-pass-v4"
    output_root = tmp_path / "analytics-v1"
    source_root.mkdir()

    payload = _payload()
    source = source_root / ("a" * 64 + ".json")
    rendered = json.dumps(payload, sort_keys=True).encode("utf-8")
    source.write_bytes(rendered)

    bad = source_root / ("b" * 64 + ".json")
    invalid = _payload()
    invalid["schemaVersion"] = 5
    bad.write_text(json.dumps(invalid), encoding="utf-8")

    manifest = build_analytics_batch(source_root, output_root)

    assert manifest["scanned"] == 2
    assert manifest["succeeded"] == 1
    assert manifest["failed"] == 1
    assert manifest["entries"][0]["replay_sha256"] == "a" * 64
    assert manifest["entries"][0]["analysis_sha256"] == hashlib.sha256(rendered).hexdigest()

    output = output_root / "aa" / ("a" * 64 + ".json")
    summary = json.loads(output.read_text(encoding="utf-8"))
    assert summary["source"]["replay_sha256"] == "a" * 64
    assert summary["source"]["analysis_sha256"] == hashlib.sha256(rendered).hexdigest()
    assert (output_root / "manifest.json").exists()


def test_batch_builder_is_deterministic(tmp_path) -> None:
    import json

    from haxlab.analysis.batch import build_analytics_batch

    source_root = tmp_path / "state-pass-v4"
    output_root = tmp_path / "analytics-v1"
    source_root.mkdir()

    for replay_sha in ("f" * 64, "0" * 64):
        (source_root / f"{replay_sha}.json").write_text(
            json.dumps(_payload(), sort_keys=True),
            encoding="utf-8",
        )

    first = build_analytics_batch(source_root, output_root)
    second = build_analytics_batch(source_root, output_root)

    assert first == second
    assert [item["replay_sha256"] for item in first["entries"]] == [
        "0" * 64,
        "f" * 64,
    ]
