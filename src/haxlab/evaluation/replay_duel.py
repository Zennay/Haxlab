from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPLAY_SCHEMA = "haxlab-elite-replay-scenario-benchmark-v1"
DUEL_SCHEMA = "haxlab-elite-replay-seeded-duel-v1"


def _scenario_key(row: dict[str, Any]) -> tuple[int, int]:
    return (
        int(row.get("scenario_index") or 0),
        int(row.get("elite_team_id") or 0),
    )


def _proxy_score(row: dict[str, Any]) -> float:
    territory = row.get("territory") or {}
    progression = row.get("progression") or {}
    return (
        0.50 * float(progression.get("elite_share") or 0.0)
        + 0.30 * float(territory.get("elite_half_rate") or 0.0)
        + 0.20 * float(territory.get("elite_attack_third_rate") or 0.0)
    )


def build_replay_seeded_duel(
    challenger: dict[str, Any],
    champion: dict[str, Any],
    *,
    tie_margin: float = 0.025,
) -> dict[str, Any]:
    if challenger.get("schema") != REPLAY_SCHEMA:
        raise ValueError("unsupported challenger replay benchmark schema")
    if champion.get("schema") != REPLAY_SCHEMA:
        raise ValueError("unsupported champion replay benchmark schema")

    challenger_scenario_sha = str(challenger.get("scenario_sha256") or "")
    champion_scenario_sha = str(champion.get("scenario_sha256") or "")
    if not challenger_scenario_sha or not champion_scenario_sha:
        raise ValueError("replay benchmark scenario_sha256 is required")
    if challenger_scenario_sha != champion_scenario_sha:
        raise ValueError("replay benchmark scenario hashes do not match")

    challenger_model = str(challenger.get("model_path") or "")
    champion_model = str(champion.get("model_path") or "")
    if not challenger_model or not champion_model:
        raise ValueError("replay benchmark model_path is required")

    challenger_rows = {
        _scenario_key(row): row for row in challenger.get("match_results") or []
    }
    champion_rows = {
        _scenario_key(row): row for row in champion.get("match_results") or []
    }
    if not challenger_rows or challenger_rows.keys() != champion_rows.keys():
        raise ValueError("replay benchmark scenario/side populations do not match")

    paired: list[dict[str, Any]] = []
    wins = draws = losses = 0
    for key in sorted(challenger_rows):
        challenger_row = challenger_rows[key]
        champion_row = champion_rows[key]
        challenger_score = _proxy_score(challenger_row)
        champion_score = _proxy_score(champion_row)
        delta = challenger_score - champion_score
        if delta > tie_margin:
            result = "win"
            wins += 1
        elif delta < -tie_margin:
            result = "loss"
            losses += 1
        else:
            result = "draw"
            draws += 1
        paired.append({
            "scenario_index": key[0],
            "challenger_team_id": key[1],
            "champion_team_id": key[1],
            "challenger_proxy_score": challenger_score,
            "champion_proxy_score": champion_score,
            "proxy_delta": delta,
            "result": result,
        })

    challenger_territory = challenger.get("territory") or {}
    champion_territory = champion.get("territory") or {}
    challenger_activity = challenger.get("policy_activity") or {}
    champion_activity = champion.get("policy_activity") or {}

    by_side: dict[str, dict[str, Any]] = {}
    challenger_sides = challenger.get("by_elite_side") or {}
    for side in ("1", "2"):
        row = challenger_sides.get(side) or {}
        by_side[side] = {
            "matches": int(row.get("matches") or 0),
            "challenger_half_rate": float(row.get("elite_half_rate") or 0.0),
            "challenger_attack_third_rate": float(
                row.get("elite_attack_third_rate") or 0.0
            ),
            "challenger_progression_share": float(
                row.get("progression_share") or 0.0
            ),
            "challenger_nonzero_movement_rate": float(
                row.get("nonzero_movement_rate") or 0.0
            ),
        }

    challenger_actions = int(challenger_activity.get("total_actions") or 0)
    challenger_kicks = int(challenger_activity.get("total_kicks") or 0)
    champion_actions = int(champion_activity.get("total_actions") or 0)
    champion_kicks = int(champion_activity.get("total_kicks") or 0)

    return {
        "schema": DUEL_SCHEMA,
        "evaluation_mode": "replay_seeded_proxy_v1",
        "challenger_model": challenger_model,
        "champion_model": champion_model,
        "scenario_sha256": challenger_scenario_sha,
        "scenario_path": str(challenger.get("scenario_path") or ""),
        "matches": len(paired),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_rate": wins / max(1, len(paired)),
        "non_loss_rate": (wins + draws) / max(1, len(paired)),
        "goals": {
            "challenger": 0,
            "champion": 0,
            "differential": 0,
        },
        "territory": {
            "challenger_half_rate": float(
                challenger_territory.get("elite_half_rate") or 0.0
            ),
            "champion_half_rate": float(
                champion_territory.get("elite_half_rate") or 0.0
            ),
            "challenger_attack_third_rate": float(
                challenger_territory.get("elite_attack_third_rate") or 0.0
            ),
            "champion_attack_third_rate": float(
                champion_territory.get("elite_attack_third_rate") or 0.0
            ),
        },
        "challenger_runtime_errors": int(
            challenger_activity.get("runtime_errors") or 0
        ),
        "champion_runtime_errors": int(
            champion_activity.get("runtime_errors") or 0
        ),
        "challenger_kick_action_rate": challenger_kicks / max(1, challenger_actions),
        "champion_kick_action_rate": champion_kicks / max(1, champion_actions),
        "challenger_nonzero_movement_rate": float(
            challenger_activity.get("nonzero_movement_rate") or 0.0
        ),
        "champion_nonzero_movement_rate": float(
            champion_activity.get("nonzero_movement_rate") or 0.0
        ),
        "challenger_progression_share": float(
            (challenger.get("progression") or {}).get("elite_share") or 0.0
        ),
        "champion_progression_share": float(
            (champion.get("progression") or {}).get("elite_share") or 0.0
        ),
        "by_challenger_side": by_side,
        "tie_margin": tie_margin,
        "match_results": paired,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-replay-seeded-duel")
    parser.add_argument("challenger", type=Path)
    parser.add_argument("champion", type=Path)
    parser.add_argument("--tie-margin", type=float, default=0.025)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    challenger = json.loads(args.challenger.read_text(encoding="utf-8"))
    champion = json.loads(args.champion.read_text(encoding="utf-8"))
    result = build_replay_seeded_duel(
        challenger,
        champion,
        tie_margin=max(0.0, float(args.tie_margin)),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
