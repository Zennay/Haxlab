from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ANALYTICS_SCHEMA = "haxlab-replay-analytics-v1"
SUPPORTED_DECODER_SCHEMA = 4
SUPPORTED_FEATURE_VERSION = "touch-chain-v1"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


def _identity(player: dict[str, Any]) -> str:
    auth_hash = player.get("authHash")
    if auth_hash:
        return f"auth:{auth_hash}"
    name = " ".join(str(player.get("name") or "").strip().split()).casefold()
    return f"name:{name}" if name else f"replay-player:{_integer(player.get('id'))}"


def _player_summary(player: dict[str, Any], sample_hz: float) -> dict[str, Any]:
    samples = _integer(player.get("samples"))
    active_seconds = samples / sample_hz if sample_hz > 0 else 0.0
    retained = (
        _integer(player.get("selfRetouches"))
        + _integer(player.get("teamTouchTransfersOut"))
    )
    turnovers = _integer(player.get("turnovers"))
    transfer_success = _integer(player.get("kickTransfersToTeammate"))
    transfer_loss = _integer(player.get("kickTransfersToOpponent"))

    return {
        "player_id": _integer(player.get("id")),
        "identity": _identity(player),
        "name": player.get("name"),
        "team_id": _integer(player.get("teamId")),
        "samples": samples,
        "active_seconds": round(active_seconds, 3),
        "average_position": {
            "x": _number(player.get("averageX")),
            "y": _number(player.get("averageY")),
        },
        "heatmap": dict(player.get("heatmap") or {}),
        "actions": {
            "input_events": _integer(player.get("inputEvents")),
            "kick_events": _integer(player.get("kickEvents")),
            "touches": _integer(player.get("touches")),
            "self_retouches": _integer(player.get("selfRetouches")),
            "team_touch_transfers": _integer(player.get("teamTouchTransfersOut")),
            "turnovers": turnovers,
            "recoveries": _integer(player.get("recoveries")),
            "kick_transfers_to_teammate": transfer_success,
            "kick_transfers_to_opponent": transfer_loss,
            "touch_goals": _integer(player.get("touchGoals")),
            "touch_assists": _integer(player.get("touchAssists")),
        },
        "rates": {
            "touch_retention": _safe_div(retained, retained + turnovers),
            "kick_transfer_completion": _safe_div(
                transfer_success,
                transfer_success + transfer_loss,
            ),
            "under_pressure_touch": _number(player.get("underPressureTouchRate")),
            "pressured_retention": _number(player.get("pressuredRetentionRate")),
        },
        "progression": {
            "average_touch": _number(player.get("averageTouchProgression")),
            "average_kick": _number(player.get("averageProgression")),
        },
    }


def _team_summary(players: list[dict[str, Any]], team_id: int) -> dict[str, Any]:
    members = [player for player in players if player["team_id"] == team_id]
    totals: dict[str, int] = defaultdict(int)
    weighted_x = 0.0
    weighted_y = 0.0
    position_weight = 0

    for player in members:
        for key, value in player["actions"].items():
            totals[key] += _integer(value)
        samples = _integer(player["samples"])
        weighted_x += _number(player["average_position"]["x"]) * samples
        weighted_y += _number(player["average_position"]["y"]) * samples
        position_weight += samples

    retained = totals["self_retouches"] + totals["team_touch_transfers"]
    transfer_attempts = (
        totals["kick_transfers_to_teammate"]
        + totals["kick_transfers_to_opponent"]
    )

    return {
        "team_id": team_id,
        "player_count": len(members),
        "players": [player["identity"] for player in members],
        "actions": dict(totals),
        "rates": {
            "touch_retention": _safe_div(retained, retained + totals["turnovers"]),
            "kick_transfer_completion": _safe_div(
                totals["kick_transfers_to_teammate"],
                transfer_attempts,
            ),
        },
        "sample_weighted_mean_player_position": {
            "x": _safe_div(weighted_x, position_weight),
            "y": _safe_div(weighted_y, position_weight),
        },
    }


def summarize_replay_v1(payload: dict[str, Any]) -> dict[str, Any]:
    schema_version = _integer(payload.get("schemaVersion"))
    feature_version = str(payload.get("featureVersion") or "")
    if schema_version != SUPPORTED_DECODER_SCHEMA:
        raise ValueError(
            f"unsupported decoder schema {schema_version}; expected {SUPPORTED_DECODER_SCHEMA}"
        )
    if feature_version != SUPPORTED_FEATURE_VERSION:
        raise ValueError(
            f"unsupported feature version {feature_version!r}; "
            f"expected {SUPPORTED_FEATURE_VERSION!r}"
        )

    simulation = payload.get("simulation") or {}
    sample_every = max(1, _integer(simulation.get("sampleEveryTicks"), 6))
    sample_hz = 60.0 / sample_every
    players = [
        _player_summary(player, sample_hz)
        for player in (payload.get("players") or [])
        if _integer(player.get("teamId")) in (1, 2)
    ]
    players.sort(key=lambda item: (item["team_id"], item["identity"]))

    team_goals = simulation.get("teamGoals") or {}
    ball = payload.get("ball") or {}

    return {
        "schema": ANALYTICS_SCHEMA,
        "source": {
            "file": payload.get("sourceFile"),
            "decoder": payload.get("decoder"),
            "decoder_schema": schema_version,
            "feature_version": feature_version,
        },
        "match": {
            "frames": _integer(payload.get("totalFrames")),
            "sample_every_ticks": sample_every,
            "sampled_state_count": _integer(simulation.get("sampledStateCount")),
            "team_goals": {
                "red": _integer(team_goals.get("red")),
                "blue": _integer(team_goals.get("blue")),
            },
            "ball": {
                "average_x": _number(ball.get("averageX")),
                "average_y": _number(ball.get("averageY")),
                "average_speed": _number(ball.get("averageSpeed")),
                "heatmap": dict(ball.get("heatmap") or {}),
            },
        },
        "players": players,
        "teams": {
            "red": _team_summary(players, 1),
            "blue": _team_summary(players, 2),
        },
        "event_totals": dict(payload.get("featureSummary") or {}),
        "limitations": [
            "kick transfer completion is not a full semantic pass metric",
            "shot attempts are not derivable reliably from touch-chain-v1",
            "team spacing/shape needs frame-level aggregate evidence in a future decoder feature version",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-analytics-v1")
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    summary = summarize_replay_v1(payload)
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
