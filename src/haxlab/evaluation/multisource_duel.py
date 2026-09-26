from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from haxlab.evaluation.replay_duel import DUEL_SCHEMA


MULTISOURCE_DUEL_SCHEMA = "haxlab-elite-multisource-duel-v1"
ARENA_SCHEMA = "haxlab-multisource-champion-arena-v2"


def _weighted_average(
    rows: list[dict[str, Any]],
    key: str,
    *,
    weight_key: str = "matches",
) -> float:
    weighted = 0.0
    total = 0
    for row in rows:
        weight = int(row.get(weight_key) or 0)
        if weight <= 0:
            continue
        weighted += float(row.get(key) or 0.0) * weight
        total += weight
    return weighted / total if total > 0 else 0.0


def _load_arena_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema") != ARENA_SCHEMA:
        raise ValueError(
            f"unsupported arena schema: {manifest.get('schema')!r}"
        )
    digest = hashlib.sha256(raw).hexdigest()
    return manifest, digest


def aggregate_replay_seeded_duels(
    duels: list[dict[str, Any]],
    *,
    arena_manifest: dict[str, Any] | None = None,
    arena_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    if not duels:
        raise ValueError("at least one replay-seeded duel is required")

    challenger_models = {
        str(row.get("challenger_model") or "") for row in duels
    }
    champion_models = {
        str(row.get("champion_model") or "") for row in duels
    }
    if "" in challenger_models or len(challenger_models) != 1:
        raise ValueError("all duel inputs must use one challenger model")
    if "" in champion_models or len(champion_models) != 1:
        raise ValueError("all duel inputs must use one champion model")

    scenario_hashes: list[str] = []
    sources: list[dict[str, Any]] = []
    combined_matches: list[dict[str, Any]] = []

    for source_index, duel in enumerate(duels, start=1):
        if duel.get("schema") != DUEL_SCHEMA:
            raise ValueError(
                f"source {source_index}: unsupported duel schema "
                f"{duel.get('schema')!r}"
            )
        scenario_sha = str(duel.get("scenario_sha256") or "")
        if not scenario_sha:
            raise ValueError(
                f"source {source_index}: scenario_sha256 is required"
            )
        if scenario_sha in scenario_hashes:
            raise ValueError(
                f"source {source_index}: duplicate scenario_sha256"
            )
        matches = int(duel.get("matches") or 0)
        if matches <= 0:
            raise ValueError(
                f"source {source_index}: duel must contain matches"
            )

        scenario_hashes.append(scenario_sha)
        wins = int(duel.get("wins") or 0)
        draws = int(duel.get("draws") or 0)
        losses = int(duel.get("losses") or 0)
        if wins + draws + losses != matches:
            raise ValueError(
                f"source {source_index}: W/D/L does not equal matches"
            )

        sources.append({
            "source_index": source_index,
            "scenario_sha256": scenario_sha,
            "matches": matches,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "match_score": (wins + 0.5 * draws) / matches,
        })
        for match in duel.get("match_results") or []:
            combined_matches.append({
                **match,
                "arena_source_index": source_index,
                "source_scenario_sha256": scenario_sha,
            })

    if arena_manifest is not None:
        expected_sources = list(arena_manifest.get("sources") or [])
        if int(arena_manifest.get("source_count") or 0) != len(duels):
            raise ValueError(
                "arena source_count does not match duel input count"
            )
        expected_hashes = [
            str(
                ((source.get("files_sha256") or {}).get("scenarios.json"))
                or ""
            )
            for source in expected_sources
        ]
        if any(not value for value in expected_hashes):
            raise ValueError(
                "arena manifest is missing scenario file hashes"
            )
        if expected_hashes != scenario_hashes:
            raise ValueError(
                "duel scenario hashes do not match frozen arena order"
            )

    total_matches = sum(int(row["matches"]) for row in sources)
    wins = sum(int(row["wins"]) for row in sources)
    draws = sum(int(row["draws"]) for row in sources)
    losses = sum(int(row["losses"]) for row in sources)

    territory_rows = [
        {
            "matches": int(duel.get("matches") or 0),
            **(duel.get("territory") or {}),
        }
        for duel in duels
    ]

    side_summary: dict[str, dict[str, Any]] = {}
    for side in ("1", "2"):
        rows = []
        for duel in duels:
            side_row = ((duel.get("by_challenger_side") or {}).get(side) or {})
            rows.append(side_row)
        side_matches = sum(int(row.get("matches") or 0) for row in rows)
        side_summary[side] = {
            "matches": side_matches,
            "challenger_half_rate": _weighted_average(
                rows, "challenger_half_rate"
            ),
            "challenger_attack_third_rate": _weighted_average(
                rows, "challenger_attack_third_rate"
            ),
            "challenger_progression_share": _weighted_average(
                rows, "challenger_progression_share"
            ),
            "challenger_nonzero_movement_rate": _weighted_average(
                rows, "challenger_nonzero_movement_rate"
            ),
        }

    weighted_rows = [
        {"matches": int(duel.get("matches") or 0), **duel}
        for duel in duels
    ]
    source_scores = [float(row["match_score"]) for row in sources]

    challenger_goals = sum(
        int((duel.get("goals") or {}).get("challenger") or 0)
        for duel in duels
    )
    champion_goals = sum(
        int((duel.get("goals") or {}).get("champion") or 0)
        for duel in duels
    )

    return {
        "schema": MULTISOURCE_DUEL_SCHEMA,
        "evaluation_mode": "multisource_replay_seeded_proxy_v1",
        "challenger_model": next(iter(challenger_models)),
        "champion_model": next(iter(champion_models)),
        "arena_manifest_sha256": arena_manifest_sha256,
        "source_count": len(sources),
        "sources": sources,
        "matches": total_matches,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "win_rate": wins / max(1, total_matches),
        "non_loss_rate": (wins + draws) / max(1, total_matches),
        "source_match_score_min": min(source_scores),
        "source_match_score_max": max(source_scores),
        "source_match_score_mean": sum(source_scores) / len(source_scores),
        "goals": {
            "challenger": challenger_goals,
            "champion": champion_goals,
            "differential": challenger_goals - champion_goals,
        },
        "territory": {
            key: _weighted_average(territory_rows, key)
            for key in (
                "challenger_half_rate",
                "champion_half_rate",
                "challenger_attack_third_rate",
                "champion_attack_third_rate",
            )
        },
        "challenger_runtime_errors": sum(
            int(duel.get("challenger_runtime_errors") or 0)
            for duel in duels
        ),
        "champion_runtime_errors": sum(
            int(duel.get("champion_runtime_errors") or 0)
            for duel in duels
        ),
        "challenger_kick_action_rate": _weighted_average(
            weighted_rows, "challenger_kick_action_rate"
        ),
        "champion_kick_action_rate": _weighted_average(
            weighted_rows, "champion_kick_action_rate"
        ),
        "challenger_nonzero_movement_rate": _weighted_average(
            weighted_rows, "challenger_nonzero_movement_rate"
        ),
        "champion_nonzero_movement_rate": _weighted_average(
            weighted_rows, "champion_nonzero_movement_rate"
        ),
        "challenger_progression_share": _weighted_average(
            weighted_rows, "challenger_progression_share"
        ),
        "champion_progression_share": _weighted_average(
            weighted_rows, "champion_progression_share"
        ),
        "by_challenger_side": side_summary,
        "match_results": combined_matches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-multisource-duel")
    parser.add_argument(
        "--duel",
        dest="duels",
        action="append",
        type=Path,
        required=True,
        help="Replay-seeded duel JSON. Repeat in frozen arena order.",
    )
    parser.add_argument(
        "--arena-manifest",
        type=Path,
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    arena_manifest, arena_sha = _load_arena_manifest(args.arena_manifest)
    duels = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.duels
    ]
    result = aggregate_replay_seeded_duels(
        duels,
        arena_manifest=arena_manifest,
        arena_manifest_sha256=arena_sha,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
