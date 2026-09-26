from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from haxlab.analysis.roles import ROLES_4V4, infer_roles_4v4


def average_team_sizes(payload: dict[str, Any]) -> dict[int, float] | None:
    simulation = payload.get("simulation") or {}
    sampled = int(simulation.get("sampledStateCount") or 0)
    if sampled <= 0:
        return None
    players = list(payload.get("players") or [])
    return {
        team_id: sum(
            int(player.get("samples") or 0)
            for player in players
            if int(player.get("teamId") or 0) == team_id
        )
        / sampled
        for team_id in (1, 2)
    }


def classify_team_size(payload: dict[str, Any]) -> str:
    sizes = average_team_sizes(payload)
    if sizes is None:
        return "unknown"
    a, b = sizes[1], sizes[2]
    rounded_a, rounded_b = round(a), round(b)
    if (
        rounded_a == rounded_b
        and 1 <= rounded_a <= 12
        and abs(a - rounded_a) <= 0.5
        and abs(b - rounded_b) <= 0.5
    ):
        return f"{rounded_a}v{rounded_b}"
    return f"mixed:{a:.2f}v{b:.2f}"


def is_true_4v4(payload: dict[str, Any]) -> bool:
    sizes = average_team_sizes(payload)
    return bool(
        sizes
        and 3.5 <= sizes[1] <= 4.5
        and 3.5 <= sizes[2] <= 4.5
    )


def _old_permissive_quality(payload: dict[str, Any]) -> bool:
    total_frames = int(payload.get("totalFrames") or 0)
    simulation = payload.get("simulation") or {}
    feature_summary = payload.get("featureSummary") or {}
    players = list(payload.get("players") or [])

    if int(payload.get("schemaVersion") or 0) < 4:
        return False
    if total_frames / 60.0 < 120.0:
        return False
    if int(simulation.get("sampledStateCount") or 0) <= 0:
        return False
    if int(feature_summary.get("touches") or 0) <= 0:
        return False

    for team_id in (1, 2):
        active = [
            player
            for player in players
            if int(player.get("teamId") or 0) == team_id
            and int(player.get("samples") or 0) > 0
        ]
        if len(active) < 4:
            return False

    roles = infer_roles_4v4(players)
    for team_id in (1, 2):
        usable = [
            player
            for player in players
            if int(player.get("teamId") or 0) == team_id
            and int(player.get("samples") or 0) > 0
            and (roles.get(int(player.get("id") or -1)) or {}).get("role")
            in ROLES_4V4
        ]
        if len(usable) < 4:
            return False
    return True


def _strict_quality(payload: dict[str, Any]) -> bool:
    return _old_permissive_quality(payload) and is_true_4v4(payload)


def audit_population(root: Path) -> dict[str, Any]:
    size_counts: Counter[str] = Counter()
    old_eligible_by_size: Counter[str] = Counter()
    strict_eligible_by_size: Counter[str] = Counter()
    scanned = readable = broken = schema_v4 = 0
    old_eligible = strict_eligible = 0

    for path in root.rglob("*.json"):
        if path.name.startswith("_"):
            continue
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            broken += 1
            continue
        readable += 1
        if int(payload.get("schemaVersion") or 0) != 4:
            continue
        schema_v4 += 1

        label = classify_team_size(payload)
        size_counts[label] += 1

        if _old_permissive_quality(payload):
            old_eligible += 1
            old_eligible_by_size[label] += 1
        if _strict_quality(payload):
            strict_eligible += 1
            strict_eligible_by_size[label] += 1

    contaminated = max(0, old_eligible - strict_eligible)
    return {
        "schema": "haxlab-replay-population-audit-v1",
        "root": str(root),
        "files_scanned": scanned,
        "readable": readable,
        "broken_or_unreadable": broken,
        "schema_v4": schema_v4,
        "team_size_distribution": dict(
            sorted(size_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "old_permissive_eligible": old_eligible,
        "strict_4v4_eligible": strict_eligible,
        "contaminated_old_eligible": contaminated,
        "contamination_rate_of_old_eligible": (
            contaminated / old_eligible if old_eligible else 0.0
        ),
        "old_eligible_by_team_size": dict(
            sorted(
                old_eligible_by_size.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "strict_eligible_by_team_size": dict(
            sorted(
                strict_eligible_by_size.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-replay-audit")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = audit_population(args.root)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
