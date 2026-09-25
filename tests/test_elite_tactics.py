from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _run_node(payload: dict) -> dict:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const payload = JSON.parse(process.argv[1]);
const player = {{
  id: 1,
  disc: {{
    pos: payload.player_pos,
    speed: {{x:0,y:0}},
  }},
}};
const gameState = {{
  physicsState: {{
    discs: [{{
      pos: payload.ball_pos,
      speed: payload.ball_speed,
    }}],
  }},
}};
const action = tactics.kickoffAction(payload.role, player, gameState);
console.log(JSON.stringify({{
  kickoff: tactics.isKickoffState(gameState),
  action,
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script, json.dumps(payload)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return json.loads(completed.stdout)


def test_st_takes_stationary_center_kickoff() -> None:
    result = _run_node(
        {
            "role": "st",
            "player_pos": {"x": -80, "y": 0},
            "ball_pos": {"x": 0, "y": 0},
            "ball_speed": {"x": 0, "y": 0},
        }
    )

    assert result["kickoff"] is True
    assert result["action"]["dirX"] == 1
    assert result["action"]["dirY"] == 0
    assert result["action"]["kick"] is False
    assert result["action"]["source"] == "kickoff_st"


def test_non_st_roles_hold_shape_during_kickoff() -> None:
    for role in ("gk", "dm", "am"):
        result = _run_node(
            {
                "role": role,
                "player_pos": {"x": -120, "y": 20},
                "ball_pos": {"x": 0, "y": 0},
                "ball_speed": {"x": 0, "y": 0},
            }
        )
        assert result["action"] == {
            "dirX": 0,
            "dirY": 0,
            "kick": False,
            "source": "kickoff_shape_hold",
        }


def test_kickoff_overlay_disengages_after_ball_moves() -> None:
    result = _run_node(
        {
            "role": "st",
            "player_pos": {"x": -20, "y": 0},
            "ball_pos": {"x": 15, "y": 0},
            "ball_speed": {"x": 1, "y": 0},
        }
    )

    assert result["kickoff"] is False
    assert result["action"] is None


def test_learned_kick_is_blocked_when_ball_is_far_away() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const player = {{disc: {{pos: {{x:0,y:0}}}}}};
const gameState = {{physicsState: {{discs: [{{pos: {{x:100,y:0}}}}]}}}};
console.log(JSON.stringify(
  tactics.enforceKickRange({{dirX:1, dirY:0, kick:true}}, player, gameState)
));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    action = json.loads(completed.stdout)
    assert action["kick"] is False
    assert action["kick_in_range"] is False


def test_learned_kick_survives_when_ball_is_in_range() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const player = {{disc: {{pos: {{x:0,y:0}}}}}};
const gameState = {{physicsState: {{discs: [{{pos: {{x:20,y:0}}}}]}}}};
console.log(JSON.stringify(
  tactics.enforceKickRange({{dirX:1, dirY:0, kick:true}}, player, gameState)
));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    action = json.loads(completed.stdout)
    assert action["kick"] is True
    assert action["kick_in_range"] is True
