from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_SCHEMA = "haxlab-human-imitation-manifest-v3"
_CANONICAL_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def _native_finite_number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(float(value))


def _valid_leaderboard_row(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    player_id = row.get("player_id")
    matches = row.get("matches")
    return (
        isinstance(player_id, str)
        and bool(player_id.strip())
        and type(matches) is int
        and matches >= 0
        and _native_finite_number(row.get("minutes"))
        and float(row["minutes"]) >= 0.0
        and _native_finite_number(row.get("rating"))
        and _native_finite_number(row.get("rating_uncertainty"))
        and float(row["rating_uncertainty"]) >= 0.0
    )


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
        if _valid_leaderboard_row(row)
        and row["matches"] >= min_matches
        and float(row["minutes"]) >= min_minutes
        and float(row["rating_uncertainty"]) <= max_uncertainty
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


def _analysis_replay_sha256(path: Path) -> str | None:
    stem = path.stem
    return stem if _CANONICAL_SHA256.fullmatch(stem) else None


def _validate_holdout_partition(modulus: int, bucket: int) -> None:
    if type(modulus) is not int or modulus < 2:
        raise ValueError("holdout_modulus must be a native integer >= 2")
    if type(bucket) is not int or bucket < 0 or bucket >= modulus:
        raise ValueError(
            "holdout_bucket must be a native integer in [0, holdout_modulus)"
        )


def _holdout_bucket(replay_sha256: str, modulus: int = 10) -> int:
    digest = hashlib.sha256(
        f"haxlab-holdout-v1:{replay_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _replay_quality(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    schema_version = payload.get("schemaVersion")
    total_frames = payload.get("totalFrames")
    players = payload.get("players")
    simulation = payload.get("simulation")
    feature_summary = payload.get("featureSummary")

    if type(schema_version) is not int:
        reasons.append("invalid_schema_version")
    elif schema_version < 4:
        reasons.append("schema_before_v4")

    if type(total_frames) is not int or total_frames < 0:
        reasons.append("invalid_total_frames")
    elif total_frames / 60.0 < 120.0:
        reasons.append("shorter_than_2m")

    if not isinstance(simulation, dict):
        reasons.append("invalid_simulation")
    else:
        sampled_state_count = simulation.get("sampledStateCount")
        if type(sampled_state_count) is not int or sampled_state_count <= 0:
            reasons.append("no_sampled_state")

    if not isinstance(players, list) or any(
        not isinstance(player, dict) for player in players
    ):
        reasons.append("invalid_players")
    elif len(players) < 4:
        reasons.append("fewer_than_4_players")

    if not isinstance(feature_summary, dict):
        reasons.append("invalid_feature_summary")
    else:
        touches = feature_summary.get("touches")
        if type(touches) is not int or touches <= 0:
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
    _validate_holdout_partition(holdout_modulus, holdout_bucket)
    leaderboard_bytes = leaderboard_path.read_bytes()
    leaderboard_sha256 = hashlib.sha256(leaderboard_bytes).hexdigest()
    leaderboard = json.loads(leaderboard_bytes)
    if not isinstance(leaderboard, dict):
        raise ValueError("leaderboard must be a JSON object")
    leaderboard_rows = leaderboard.get("rows")
    if not isinstance(leaderboard_rows, list):
        raise ValueError("leaderboard rows must be a JSON list")
    selected_players = select_players(
        leaderboard_rows,
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
    seen_replay_sha256: set[str] = set()

    for path in analysis_root.rglob("*.json"):
        if path.name.startswith("_"):
            continue
        scanned += 1
        replay_sha256 = _analysis_replay_sha256(path)
        if replay_sha256 is None or path.is_symlink() or not path.is_file():
            rejected += 1
            rejection_reasons.update(["invalid_analysis_provenance"])
            continue
        if replay_sha256 in seen_replay_sha256:
            raise ValueError(
                f"duplicate analysis provenance for replay {replay_sha256}"
            )
        seen_replay_sha256.add(replay_sha256)
        try:
            analysis_bytes = path.read_bytes()
            analysis_sha256 = hashlib.sha256(analysis_bytes).hexdigest()
            payload = json.loads(analysis_bytes)
        except (OSError, json.JSONDecodeError):
            rejected += 1
            continue
        if not isinstance(payload, dict):
            rejected += 1
            rejection_reasons.update(["invalid_analysis_payload"])
            continue

        quality_ok, quality_reasons = _replay_quality(payload)
        if not quality_ok:
            rejected += 1
            rejection_reasons.update(quality_reasons)
            continue

        selected_in_replay: list[dict[str, Any]] = []
        for player in payload["players"]:
            identity = _identity_key(player)
            team_id = player.get("teamId")
            samples = player.get("samples")
            replay_player_id = player.get("id")
            if (
                identity in selected_ids
                and type(team_id) is int
                and team_id in (1, 2)
                and type(samples) is int
                and samples > 0
                and type(replay_player_id) is int
                and replay_player_id >= 0
            ):
                selected_in_replay.append(
                    {
                        "replay_player_id": replay_player_id,
                        "identity": identity,
                        "samples": samples,
                    }
                )

        if not selected_in_replay:
            continue

        selected_in_replay.sort(
            key=lambda row: (row["replay_player_id"], row["identity"])
        )
        selected_identity_ids = sorted(
            {row["identity"] for row in selected_in_replay}
        )

        raw_path = (
            raw_root
            / replay_sha256[:2]
            / replay_sha256[2:4]
            / f"{replay_sha256}.hbr2"
        )
        conservative = max(
            score_by_id[player_id] for player_id in selected_identity_ids
        )
        entry = {
            "replay_sha256": replay_sha256,
            "raw_path": str(raw_path),
            "analysis_path": str(path),
            "analysis_sha256": analysis_sha256,
            "analysis_size_bytes": len(analysis_bytes),
            "total_frames": int(payload.get("totalFrames") or 0),
            "duration_seconds": round(
                int(payload.get("totalFrames") or 0) / 60.0,
                3,
            ),
            "selected_player_ids": selected_identity_ids,
            "selected_players": selected_in_replay,
            "selected_player_count": len(selected_in_replay),
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
        "leaderboard_sha256": leaderboard_sha256,
        "leaderboard_size_bytes": len(leaderboard_bytes),
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
