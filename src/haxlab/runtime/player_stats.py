from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, pstdev


def _name_key(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = " ".join(str(name).strip().split())
    return cleaned.casefold() or None


def _identity_key(player: dict) -> str | None:
    auth_hash = player.get("authHash")
    if auth_hash:
        return f"auth:{auth_hash}"
    name = _name_key(player.get("name"))
    return f"name:{name}" if name else None


def _safe_div(num: float, den: float) -> float:
    return num / den if den > 0 else 0.0


def _z(values: list[float]) -> list[float]:
    if not values:
        return []
    avg = mean(values)
    sd = pstdev(values)
    if sd <= 1e-12:
        return [0.0 for _ in values]
    return [(value - avg) / sd for value in values]


def collect(root: Path) -> list[dict]:
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "name": "",
            "matches": 0,
            "samples": 0,
            "nearest_ball_samples": 0,
            "close_ball_samples": 0,
            "input_events": 0,
            "kick_events": 0,
            "kick_pressed_inputs": 0,
            "touches": 0,
            "self_retouches": 0,
            "team_touch_transfers_out": 0,
            "turnovers": 0,
            "recoveries": 0,
            "pressured_transitions": 0,
            "retained_under_pressure": 0,
            "touch_progression_events": 0,
            "touch_progression_sum": 0.0,
            "touch_goals": 0,
            "touch_assists": 0,
        }
    )

    for path in root.rglob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if int(payload.get("schemaVersion") or 0) not in (3, 4):
            continue

        seen: set[str] = set()
        for player in payload.get("players") or []:
            key = _identity_key(player)
            if key is None:
                continue

            row = totals[key]
            if not row["name"]:
                row["name"] = str(player.get("name")).strip()

            if key not in seen:
                row["matches"] += 1
                seen.add(key)

            row["samples"] += int(player.get("samples") or 0)
            row["nearest_ball_samples"] += int(player.get("nearestBallSamples") or 0)
            row["close_ball_samples"] += int(player.get("closeBallSamples") or 0)
            row["input_events"] += int(player.get("inputEvents") or 0)
            row["kick_events"] += int(player.get("kickEvents") or 0)
            row["kick_pressed_inputs"] += int(player.get("kickPressedInputs") or 0)
            row["touches"] += int(player.get("touches") or 0)
            row["self_retouches"] += int(player.get("selfRetouches") or 0)
            row["team_touch_transfers_out"] += int(
                player.get("teamTouchTransfersOut") or 0
            )
            row["turnovers"] += int(player.get("turnovers") or 0)
            row["recoveries"] += int(player.get("recoveries") or 0)
            row["pressured_transitions"] += int(
                player.get("pressuredTransitions") or 0
            )
            row["retained_under_pressure"] += int(
                player.get("retainedUnderPressure") or 0
            )
            row["touch_progression_events"] += int(
                player.get("touchProgressionEvents") or 0
            )
            row["touch_progression_sum"] += float(
                player.get("touchProgressionSum") or 0.0
            )
            row["touch_goals"] += int(player.get("touchGoals") or 0)
            row["touch_assists"] += int(player.get("touchAssists") or 0)

    rows: list[dict] = []
    for row in totals.values():
        active_minutes = row["samples"] / 600.0  # 10 Hz state sampling.
        retained_touch_transitions = (
            row["self_retouches"] + row["team_touch_transfers_out"]
        )
        touch_transitions = retained_touch_transitions + row["turnovers"]
        rows.append(
            {
                **row,
                "active_minutes": active_minutes,
                "nearest_ball_pct": 100.0
                * _safe_div(row["nearest_ball_samples"], row["samples"]),
                "close_ball_pct": 100.0
                * _safe_div(row["close_ball_samples"], row["samples"]),
                "kicks_per_min": _safe_div(row["kick_events"], active_minutes),
                "inputs_per_min": _safe_div(row["input_events"], active_minutes),
                "touches_per_min": _safe_div(row["touches"], active_minutes),
                "touch_retention_pct": 100.0
                * _safe_div(retained_touch_transitions, touch_transitions),
                "pressured_retention_pct": 100.0
                * _safe_div(
                    row["retained_under_pressure"],
                    row["pressured_transitions"],
                ),
                "touch_progression": _safe_div(
                    row["touch_progression_sum"],
                    row["touch_progression_events"],
                ),
                "recoveries_per_min": _safe_div(
                    row["recoveries"],
                    active_minutes,
                ),
                "goal_contrib_per_10": 10.0
                * _safe_div(
                    row["touch_goals"] + row["touch_assists"],
                    active_minutes,
                ),
            }
        )
    return rows


def add_proxy_score(rows: list[dict]) -> None:
    kick_z = _z([row["kicks_per_min"] for row in rows])
    close_z = _z([row["close_ball_pct"] for row in rows])
    nearest_z = _z([row["nearest_ball_pct"] for row in rows])

    for row, zk, zc, zn in zip(rows, kick_z, close_z, nearest_z):
        # This is deliberately an involvement proxy, not a skill estimate.
        raw = 0.50 * zk + 0.30 * zc + 0.20 * zn
        reliability = min(1.0, math.sqrt(max(0, row["matches"]) / 30.0))
        row["involvement_score"] = raw * reliability


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-players")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--min-matches", type=int, default=20)
    parser.add_argument("--min-minutes", type=float, default=30.0)
    parser.add_argument(
        "--sort",
        choices=[
            "involvement",
            "matches",
            "minutes",
            "kicks",
            "near-ball",
            "close-ball",
            "touches",
            "retention",
            "recoveries",
            "progression",
        ],
        default="involvement",
    )
    args = parser.parse_args()

    rows = [
        row
        for row in collect(args.root)
        if row["matches"] >= max(1, args.min_matches)
        and row["active_minutes"] >= max(0.0, args.min_minutes)
    ]
    add_proxy_score(rows)

    sort_keys = {
        "involvement": "involvement_score",
        "matches": "matches",
        "minutes": "active_minutes",
        "kicks": "kicks_per_min",
        "near-ball": "nearest_ball_pct",
        "close-ball": "close_ball_pct",
        "touches": "touches_per_min",
        "retention": "touch_retention_pct",
        "recoveries": "recoveries_per_min",
        "progression": "touch_progression",
    }
    key = sort_keys[args.sort]
    rows.sort(key=lambda row: (row[key], row["matches"]), reverse=True)
    rows = rows[: max(1, args.top)]

    print(
        "PRELIMINARY PLAYER STATS — involvement is NOT the final HaxLab skill rating."
    )
    print(
        "Identity is currently grouped by normalized display name; aliases/collisions "
        "are not resolved yet."
    )
    print()
    has_touch_features = any(row["touches"] > 0 for row in rows)
    if has_touch_features:
        header = (
            f"{'#':>3}  {'Player':<22} {'M':>5} {'Min':>8} "
            f"{'Touch/m':>8} {'Ret%':>7} {'Prog':>8} "
            f"{'Rec/m':>7} {'G+A/10':>8}"
        )
    else:
        header = (
            f"{'#':>3}  {'Player':<24} {'M':>5} {'Min':>8} "
            f"{'Kick/m':>8} {'Near%':>7} {'Close%':>7} {'Inv':>8}"
        )
    print(header)
    print("-" * len(header))
    for index, row in enumerate(rows, 1):
        if has_touch_features:
            name = row["name"][:22]
            print(
                f"{index:>3}  {name:<22} "
                f"{row['matches']:>5} "
                f"{row['active_minutes']:>8.1f} "
                f"{row['touches_per_min']:>8.2f} "
                f"{row['touch_retention_pct']:>7.2f} "
                f"{row['touch_progression']:>8.2f} "
                f"{row['recoveries_per_min']:>7.2f} "
                f"{row['goal_contrib_per_10']:>8.2f}"
            )
        else:
            name = row["name"][:24]
            print(
                f"{index:>3}  {name:<24} "
                f"{row['matches']:>5} "
                f"{row['active_minutes']:>8.1f} "
                f"{row['kicks_per_min']:>8.2f} "
                f"{row['nearest_ball_pct']:>7.2f} "
                f"{row['close_ball_pct']:>7.2f} "
                f"{row['involvement_score']:>8.3f}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
