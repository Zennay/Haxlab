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


def test_stall_guard_requires_distance_and_consecutive_stationary_decisions() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
console.log(JSON.stringify({{
  early: tactics.shouldRecoverFromStall({{dirX:0,dirY:0}}, 300, 2),
  ready: tactics.shouldRecoverFromStall({{dirX:0,dirY:0}}, 300, 3),
  near: tactics.shouldRecoverFromStall({{dirX:0,dirY:0}}, 50, 10),
  moving: tactics.shouldRecoverFromStall({{dirX:1,dirY:0}}, 300, 10),
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result == {
        "early": False,
        "ready": True,
        "near": False,
        "moving": False,
    }


def test_recovery_targets_preserve_gk_dm_am_st_spine() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const roles = ["gk","dm","am","st"];
const gameState = {{
  physicsState: {{
    discs: [{{pos: {{x: 80, y: 40}}, speed: {{x:0,y:0}}}}],
  }},
}};
const rows = roles.map((role) => {{
  const player = {{disc: {{pos: {{x:-100,y:0}}, speed: {{x:0,y:0}}}}}};
  return tactics.recoveryAction(role, player, gameState, 1, {{stadiumWidth:800}});
}});
console.log(JSON.stringify(rows));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    rows = json.loads(completed.stdout)
    xs = [row["target_canonical_x"] for row in rows]
    assert xs == sorted(xs)
    assert rows[0]["source"] == "closed_loop_recovery"
    assert rows[-1]["target_canonical_x"] > rows[1]["target_canonical_x"]


def test_future_motion_assist_only_breaks_confident_far_stall() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const base = {{
  dir_x: 0,
  dir_y: 0,
  kick: false,
  future_head_available: true,
  future_dir_x: 1,
  future_dir_y: -1,
  future_direction_probability: 0.72,
}};
console.log(JSON.stringify({{
  apply: tactics.futureMotionAssist(base, 200),
  near: tactics.futureMotionAssist(base, 30),
  lowConfidence: tactics.futureMotionAssist(
    {{...base, future_direction_probability:0.2}},
    200,
  ),
  moving: tactics.futureMotionAssist({{...base, dir_x:1}}, 200),
}}));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["apply"]["dir_x"] == 1
    assert result["apply"]["dir_y"] == -1
    assert result["apply"]["future_assist_applied"] is True
    assert result["apply"]["source"] == "learned_future_motion"
    assert result["near"]["dir_x"] == 0
    assert result["lowConfidence"]["dir_x"] == 0
    assert result["moving"]["dir_x"] == 1
    assert "future_assist_applied" not in result["moving"]


def test_future_motion_assist_ignores_legacy_model_actions() -> None:
    repo = Path(__file__).parents[1]
    script = f"""
const tactics = require({json.dumps(str(repo / "tools" / "elite_tactics.js"))});
const action = {{
  dir_x: 0,
  dir_y: 0,
  kick: false,
  future_head_available: false,
  future_dir_x: 1,
  future_dir_y: 0,
  future_direction_probability: 0.99,
}};
console.log(JSON.stringify(tactics.futureMotionAssist(action, 300)));
"""
    completed = subprocess.run(
        ["node", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["dir_x"] == 0
    assert "future_assist_applied" not in result
