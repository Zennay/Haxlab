from __future__ import annotations

from collections import defaultdict
from typing import Any

ROLES_4V4 = ("gk", "dm", "am", "st")
ROLE_LABELS = {
    "gk": "GK",
    "dm": "DM",
    "am": "AM",
    "st": "ST",
    "unknown": "Unknown",
}


def attack_axis_x(team_id: int, x: float) -> float:
    """Convert world X to a common axis where larger means closer to attack."""
    if team_id == 1:
        return float(x)
    if team_id == 2:
        return -float(x)
    return 0.0


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def infer_roles_4v4(players: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Infer GK/DM/AM/ST from team-relative average longitudinal position.

    HaxLab's target environment is fixed 4v4. Exact four-player lineups receive
    the highest confidence. Replays with joins/substitutions still get a usable
    fallback: the four largest appearances define role anchors and shorter
    appearances are attached to the nearest anchor with reduced confidence.
    """
    by_team: dict[int, list[dict[str, Any]]] = defaultdict(list)
    result: dict[int, dict[str, Any]] = {}

    for player in players:
        player_id = player.get("id")
        team_id = int(player.get("teamId") or 0)
        average_x = player.get("averageX")
        samples = int(player.get("samples") or 0)
        if player_id is None:
            continue
        if team_id not in (1, 2) or average_x is None or samples <= 0:
            result[int(player_id)] = {
                "role": "unknown",
                "confidence": 0.0,
                "attack_x": None,
                "reason": "missing_team_or_position_evidence",
            }
            continue
        by_team[team_id].append(
            {
                "id": int(player_id),
                "samples": samples,
                "attack_x": attack_axis_x(team_id, float(average_x)),
            }
        )

    for team_id, rows in by_team.items():
        if len(rows) < 4:
            ordered = sorted(rows, key=lambda row: row["attack_x"])
            for index, row in enumerate(ordered):
                if len(ordered) == 1:
                    role_index = 1
                else:
                    role_index = round(index * 3 / (len(ordered) - 1))
                result[row["id"]] = {
                    "role": ROLES_4V4[role_index],
                    "confidence": 0.25,
                    "attack_x": row["attack_x"],
                    "reason": f"incomplete_{len(rows)}_player_team",
                }
            continue

        core = sorted(rows, key=lambda row: row["samples"], reverse=True)[:4]
        core.sort(key=lambda row: row["attack_x"])
        xs = [row["attack_x"] for row in core]
        gaps = [max(0.0, xs[i + 1] - xs[i]) for i in range(3)]
        span = max(1.0, xs[-1] - xs[0])
        expected_gap = span / 3.0
        separation = sum(
            _clamp(gap / max(20.0, expected_gap), 0.0, 1.0) for gap in gaps
        ) / 3.0
        exact_bonus = 0.15 if len(rows) == 4 else 0.0
        core_confidence = _clamp(0.55 + 0.30 * separation + exact_bonus)

        anchors: list[tuple[str, float]] = []
        for role, row in zip(ROLES_4V4, core):
            anchors.append((role, row["attack_x"]))
            result[row["id"]] = {
                "role": role,
                "confidence": round(core_confidence, 4),
                "attack_x": row["attack_x"],
                "reason": "exact_4v4_order" if len(rows) == 4 else "core_4v4_order",
            }

        core_ids = {row["id"] for row in core}
        for row in rows:
            if row["id"] in core_ids:
                continue
            role, anchor_x = min(anchors, key=lambda item: abs(row["attack_x"] - item[1]))
            distance = abs(row["attack_x"] - anchor_x)
            confidence = _clamp(0.45 - distance / 400.0, 0.15, 0.45)
            result[row["id"]] = {
                "role": role,
                "confidence": round(confidence, 4),
                "attack_x": row["attack_x"],
                "reason": "substitute_nearest_role_anchor",
            }

    return result


def role_map_4v4(players: list[dict[str, Any]]) -> dict[int, str]:
    return {
        player_id: row["role"]
        for player_id, row in infer_roles_4v4(players).items()
    }
