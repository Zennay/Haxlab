from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "haxlab-human-imitation-manifest-v2"


def _name_key(name: str | None) -> str | None:
    if not name:
        return None
    cleaned = " ".join(str(name).strip().split())
    return cleaned.casefold() or None


def _identity_key(player: dict[str, Any]) -> str | None:
    auth_hash = player.get("authHash")
    if auth_hash:
        return f"auth:{auth_hash}"
    name = _name_key(player.get("name"))
    return f"name:{name}" if name else None


def _conservative_score(row: dict[str, Any]) -> float:
    return float(row.get("rating", 0.0)) - float(
        row.get("rating_uncertainty", 0.0)
    )


def select_players(
    leaderboard_rows: list[dict[str, Any]],
    *,
    top_fraction_per_role: float = 0.25,
    min_players_per_role: int = 5,
    min_matches: int = 30,
    min_minutes: float = 120.0,
    max_uncertainty: float = 1.5,
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in leaderboard_rows
        if int(row.get("matches", 0)) >= min_matches
        and float(row.get("minutes", 0.0)) >= min_minutes
        and float(row.get("rating_uncertainty", math.inf)) <= max_uncertainty
    ]

    by_role: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_role[str(row.get("role") or "unknown")].append(row)

    selected: list[dict[str, Any]] = []
    for role, rows in sorted(by_role.items()):
        rows.sort(
            key=lambda row: (
                _conservative_score(row),
                float(row.get("rating", 0.0)),
                int(row.get("matches", 0)),
            ),
            reverse=True,
        )
        count = max(
            min_players_per_role,
            math.ceil(len(rows) * max(0.0, min(1.0, top_fraction_per_role))),
        )
        for row in rows[: min(len(rows), count)]:
            selected.append(
                {
                    "player_id": row["player_id"],
                    "name": row.get("name"),
                    "role": role,
                    "rating": float(row.get("rating", 0.0)),
                    "rating_uncertainty": float(
                        row.get("rating_uncertainty", 0.0)
                    ),
                    "conservative_score": _conservative_score(row),
                    "matches": int(row.get("matches", 0)),
                    "minutes": float(row.get("minutes", 0.0)),
                }
            )

    selected.sort(
        key=lambda row: (
            row["conservative_score"],
            row["rating"],
            row["matches"],
        ),
        reverse=True,
    )
    return selected


def _holdout_bucket(replay_sha256: str, modulus: int = 10) -> int:
    digest = hashlib.sha256(
        f"haxlab-holdout-v1:{replay_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _replay_quality(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    total_frames = int(payload.get("totalFrames") or 0)
    duration_seconds = total_frames / 60.0
    players = list(payload.get("players") or [])
    simulation = payload.get("simulation") or {}
    feature_summary = payload.get("featureSummary") or {}

    if int(payload.get("schemaVersion") or 0) < 4:
        reasons.append("schema_before_v4")
    if duration_seconds < 120.0:
        reasons.append("shorter_than_2m")
    if int(simulation.get("sampledStateCount") or 0) <= 0:
        reasons.append("no_sampled_state")
    if len(players) < 4:
        reasons.append("fewer_than_4_players")
    if int(feature_summary.get("touches") or 0) <= 0:
        reasons.append("no_touch_evidence")

    return not reasons, reasons


def build_training_manifest(
    *,
    analysis_root: Path,
    leaderboard_path: Path,
    raw_root: Path,
    top_fraction_per_role: float = 0.25,
    min_players_per_role: int = 5,
    min_matches: int = 30,
    min_minutes: float = 120.0,
    max_uncertainty: float = 1.5,
    holdout_modulus: int = 10,
    holdout_bucket: int = 0,
) -> dict[str, Any]:
    leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    selected_players = select_players(
        list(leaderboard.get("rows") or []),
        top_fraction_per_role=top_fraction_per_role,
        min_players_per_role=min_players_per_role,
        min_matches=min_matches,
        min_minutes=min_minutes,
        max_uncertainty=max_uncertainty,
    )
    selected_ids = {row["player_id"] for row in selected_players}
    score_by_id = {
        row["player_id"]: row["conservative_score"] for row in selected_players
    }

    train: list[dict[str, Any]] = []
    holdout: list[dict[str, Any]] = []
    rejected = 0
    rejection_reasons: Counter[str] = Counter()
    scanned = 0

    for path in analysis_root.rglob("*.json"):
        if path.name.startswith("_"):
            continue
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rejected += 1
            continue

        quality_ok, quality_reasons = _replay_quality(payload)
        if not quality_ok:
            rejected += 1
            rejection_reasons.update(quality_reasons)
            continue

        selected_in_replay: list[str] = []
        for player in payload.get("players") or []:
            identity = _identity_key(player)
            if identity in selected_ids:
                selected_in_replay.append(identity)

        if not selected_in_replay:
            continue

        replay_sha256 = path.stem
        raw_path = (
            raw_root
            / replay_sha256[:2]
            / replay_sha256[2:4]
            / f"{replay_sha256}.hbr2"
        )
        conservative = max(
            score_by_id[player_id] for player_id in selected_in_replay
        )
        entry = {
            "replay_sha256": replay_sha256,
            "raw_path": str(raw_path),
            "analysis_path": str(path),
            "total_frames": int(payload.get("totalFrames") or 0),
            "duration_seconds": round(
                int(payload.get("totalFrames") or 0) / 60.0,
                3,
            ),
            "selected_player_ids": sorted(set(selected_in_replay)),
            "selected_player_count": len(set(selected_in_replay)),
            "quality_reasons": quality_reasons,
            "example_weight": round(
                max(0.25, min(2.0, 1.0 + (conservative - 50.0) / 10.0)),
                4,
            ),
        }

        if _holdout_bucket(replay_sha256, holdout_modulus) == holdout_bucket:
            holdout.append(entry)
        else:
            train.append(entry)

    train.sort(key=lambda row: row["replay_sha256"])
    holdout.sort(key=lambda row: row["replay_sha256"])

    return {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_version": leaderboard.get("analysis_version"),
        "analysis_root": str(analysis_root),
        "leaderboard_path": str(leaderboard_path),
        "raw_root": str(raw_root),
        "selection": {
            "top_fraction_per_role": top_fraction_per_role,
            "min_players_per_role": min_players_per_role,
            "min_matches": min_matches,
            "min_minutes": min_minutes,
            "max_uncertainty": max_uncertainty,
            "holdout_modulus": holdout_modulus,
            "holdout_bucket": holdout_bucket,
        },
        "selected_players": selected_players,
        "stats": {
            "analysis_files_scanned": scanned,
            "quality_rejected": rejected,
            "quality_rejection_reasons": dict(
                sorted(rejection_reasons.items())
            ),
            "selected_player_count": len(selected_players),
            "train_replay_count": len(train),
            "holdout_replay_count": len(holdout),
        },
        "train_replays": train,
        "holdout_replays": holdout,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-training-manifest")
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument(
        "--leaderboard",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/leaderboards/state-pass-v4.json"
        ),
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("/var/lib/haxlab/raw/replays"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/"
            "human-imitation-state-pass-v4.json"
        ),
    )
    parser.add_argument("--top-fraction-per-role", type=float, default=0.25)
    parser.add_argument("--min-players-per-role", type=int, default=5)
    parser.add_argument("--min-matches", type=int, default=30)
    parser.add_argument("--min-minutes", type=float, default=120.0)
    parser.add_argument("--max-uncertainty", type=float, default=1.5)
    parser.add_argument("--holdout-modulus", type=int, default=10)
    parser.add_argument("--holdout-bucket", type=int, default=0)
    args = parser.parse_args()

    manifest = build_training_manifest(
        analysis_root=args.analysis_root,
        leaderboard_path=args.leaderboard,
        raw_root=args.raw_root,
        top_fraction_per_role=args.top_fraction_per_role,
        min_players_per_role=max(1, args.min_players_per_role),
        min_matches=max(1, args.min_matches),
        min_minutes=max(0.0, args.min_minutes),
        max_uncertainty=max(0.0, args.max_uncertainty),
        holdout_modulus=max(2, args.holdout_modulus),
        holdout_bucket=max(0, args.holdout_bucket),
    )
    if manifest["selection"]["holdout_bucket"] >= manifest["selection"]["holdout_modulus"]:
        raise SystemExit("holdout-bucket must be smaller than holdout-modulus")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(manifest["stats"], indent=2, sort_keys=True))
    print(f"manifest: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
