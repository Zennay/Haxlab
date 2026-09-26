from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.analysis.roles import infer_roles_4v4


ELITE_MANIFEST_SCHEMA = "haxlab-elite-imitation-manifest-v1"
ROLE_IDS = {"gk": 0, "dm": 1, "am": 2, "st": 3}
VALID_ROLES = tuple(ROLE_IDS)


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


def _canonical_player_identity(
    player: dict[str, Any],
    aliases: dict[str, str],
) -> str | None:
    name = _name_key(player.get("name"))
    if name:
        target = aliases.get(name)
        if target:
            return f"alias:{target}"
        if name in set(aliases.values()):
            return f"alias:{name}"
    return _identity_key(player)


def _conservative_score(row: dict[str, Any]) -> float:
    return float(row.get("rating", 0.0)) - float(row.get("rating_uncertainty", 0.0))


def _skill_weight(conservative_score: float) -> float:
    # A small but meaningful preference for genuinely elite evidence.
    return max(0.65, min(1.75, 1.0 + (conservative_score - 50.0) / 12.0))


def load_aliases(path: Path | None) -> dict[str, str]:
    if path is None or not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases") if isinstance(payload, dict) else None
    if aliases is None and isinstance(payload, dict):
        aliases = payload
    result: dict[str, str] = {}
    for source, target in (aliases or {}).items():
        source_key = _name_key(str(source))
        target_key = _name_key(str(target))
        if source_key and target_key:
            result[source_key] = target_key
    return result


def _canonical_identity(
    row: dict[str, Any],
    aliases: dict[str, str],
) -> str:
    player_id = str(row["player_id"])
    name = _name_key(row.get("name"))
    if not name:
        return player_id

    target = aliases.get(name)
    if target:
        return f"alias:{target}"

    # Canonical display names that are alias targets must collapse into the
    # exact same identity as their alternate spelling. This makes
    # misio/sekai and sw1zy/swizy one training identity instead of two.
    if name in set(aliases.values()):
        return f"alias:{name}"

    return player_id


def select_elite_players(
    leaderboard_rows: list[dict[str, Any]],
    *,
    aliases: dict[str, str] | None = None,
    top_fraction_per_role: float = 0.20,
    min_players_per_role: int = 4,
    min_matches: int = 30,
    min_minutes: float = 120.0,
    max_uncertainty: float = 1.5,
) -> list[dict[str, Any]]:
    aliases = aliases or {}
    eligible = [
        row
        for row in leaderboard_rows
        if str(row.get("role") or "").lower() in VALID_ROLES
        and int(row.get("matches", 0)) >= min_matches
        and float(row.get("minutes", 0.0)) >= min_minutes
        and float(row.get("rating_uncertainty", math.inf)) <= max_uncertainty
    ]

    by_role_canonical: defaultdict[
        str, defaultdict[str, list[dict[str, Any]]]
    ] = defaultdict(lambda: defaultdict(list))
    for row in eligible:
        role = str(row["role"]).lower()
        canonical = _canonical_identity(row, aliases)
        by_role_canonical[role][canonical].append(row)

    selected: list[dict[str, Any]] = []
    for role in VALID_ROLES:
        canonical_groups = by_role_canonical.get(role, {})
        ranked_groups: list[
            tuple[dict[str, Any], str, list[dict[str, Any]]]
        ] = []
        for canonical, source_rows in canonical_groups.items():
            representative = max(
                source_rows,
                key=lambda row: (
                    _conservative_score(row),
                    float(row.get("rating", 0.0)),
                    int(row.get("matches", 0)),
                ),
            )
            ranked_groups.append((representative, canonical, source_rows))

        ranked_groups.sort(
            key=lambda item: (
                _conservative_score(item[0]),
                float(item[0].get("rating", 0.0)),
                int(item[0].get("matches", 0)),
            ),
            reverse=True,
        )
        if not ranked_groups:
            continue

        count = max(
            min_players_per_role,
            math.ceil(
                len(ranked_groups)
                * max(0.0, min(1.0, top_fraction_per_role))
            ),
        )
        for representative, canonical, source_rows in ranked_groups[
            : min(len(ranked_groups), count)
        ]:
            canonical_conservative = _conservative_score(representative)
            canonical_weight = _skill_weight(canonical_conservative)
            for row in source_rows:
                source_conservative = _conservative_score(row)
                selected.append(
                    {
                        "player_id": str(row["player_id"]),
                        "canonical_identity": canonical,
                        "name": row.get("name"),
                        "role": role,
                        "role_id": ROLE_IDS[role],
                        "rating": float(row.get("rating", 0.0)),
                        "rating_uncertainty": float(
                            row.get("rating_uncertainty", 0.0)
                        ),
                        "source_conservative_score": source_conservative,
                        "conservative_score": canonical_conservative,
                        "skill_weight": canonical_weight,
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


def _split_bucket(replay_sha256: str, modulus: int = 20) -> int:
    digest = hashlib.sha256(
        f"haxlab-elite-split-v1:{replay_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _replay_quality(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    total_frames = int(payload.get("totalFrames") or 0)
    duration_seconds = total_frames / 60.0
    simulation = payload.get("simulation") or {}
    feature_summary = payload.get("featureSummary") or {}
    players = list(payload.get("players") or [])

    if int(payload.get("schemaVersion") or 0) < 4:
        reasons.append("schema_before_v4")
    if duration_seconds < 120.0:
        reasons.append("shorter_than_2m")
    if int(simulation.get("sampledStateCount") or 0) <= 0:
        reasons.append("no_sampled_state")
    if int(feature_summary.get("touches") or 0) <= 0:
        reasons.append("no_touch_evidence")

    active_by_team = Counter(
        int(player.get("teamId") or 0)
        for player in players
        if int(player.get("samples") or 0) > 0
        and int(player.get("teamId") or 0) in (1, 2)
    )
    if active_by_team[1] < 4 or active_by_team[2] < 4:
        reasons.append("not_4v4_evidence")

    role_evidence = infer_roles_4v4(players)
    for team_id in (1, 2):
        active = [
            player
            for player in players
            if int(player.get("teamId") or 0) == team_id
            and int(player.get("samples") or 0) > 0
        ]
        usable = [
            player
            for player in active
            if role_evidence.get(int(player.get("id") or -1), {}).get("role")
            in VALID_ROLES
        ]
        if len(usable) < 4:
            reasons.append(f"unusable_role_evidence_team_{team_id}")

    return not reasons, reasons


def build_elite_manifest(
    *,
    analysis_root: Path,
    leaderboard_path: Path,
    raw_root: Path,
    aliases_path: Path | None = None,
    top_fraction_per_role: float = 0.20,
    min_players_per_role: int = 4,
    min_matches: int = 30,
    min_minutes: float = 120.0,
    max_uncertainty: float = 1.5,
    split_modulus: int = 20,
    validation_buckets: tuple[int, ...] = (1, 2),
    holdout_buckets: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    leaderboard = json.loads(leaderboard_path.read_text(encoding="utf-8"))
    aliases = load_aliases(aliases_path)
    selected_players = select_elite_players(
        list(leaderboard.get("rows") or []),
        aliases=aliases,
        top_fraction_per_role=top_fraction_per_role,
        min_players_per_role=min_players_per_role,
        min_matches=min_matches,
        min_minutes=min_minutes,
        max_uncertainty=max_uncertainty,
    )
    selected_by_id: dict[str, dict[str, Any]] = {}
    for row in selected_players:
        selected_by_id[str(row["player_id"])] = row
        selected_by_id[str(row["canonical_identity"])] = row

    split_names = {
        bucket: "holdout" for bucket in holdout_buckets
    }
    split_names.update({bucket: "validation" for bucket in validation_buckets})

    splits: dict[str, list[dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "holdout": [],
    }
    rejected = 0
    rejection_reasons: Counter[str] = Counter()
    scanned = 0
    selected_replays = 0

    for path in analysis_root.rglob("*.json"):
        if path.name.startswith("_"):
            continue
        scanned += 1
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rejected += 1
            rejection_reasons["unreadable_json"] += 1
            continue

        quality_ok, quality_reasons = _replay_quality(payload)
        if not quality_ok:
            rejected += 1
            rejection_reasons.update(quality_reasons)
            continue

        assignments = infer_roles_4v4(list(payload.get("players") or []))
        selected_in_replay: list[dict[str, Any]] = []
        for player in payload.get("players") or []:
            source_identity = _identity_key(player)
            canonical_source_identity = _canonical_player_identity(
                player,
                aliases,
            )
            selected = selected_by_id.get(canonical_source_identity or "")
            if selected is None:
                selected = selected_by_id.get(source_identity or "")
            replay_player_id = player.get("id")
            if (
                selected is None
                or replay_player_id is None
                or int(player.get("samples") or 0) <= 0
                or int(player.get("teamId") or 0) not in (1, 2)
            ):
                continue

            replay_role = assignments.get(int(replay_player_id), {}).get("role")
            replay_role_confidence = float(
                assignments.get(int(replay_player_id), {}).get("confidence", 0.0)
            )
            # The population leaderboard role selects the elite pool, while the
            # replay-local role conditions the policy for this actual appearance.
            if replay_role not in VALID_ROLES or replay_role_confidence <= 0.0:
                continue

            selected_in_replay.append(
                {
                    "replay_player_id": int(replay_player_id),
                    "source_identity": source_identity,
                    "canonical_source_identity": canonical_source_identity,
                    "identity": selected["canonical_identity"],
                    "name": player.get("name"),
                    "role": replay_role,
                    "role_id": ROLE_IDS[replay_role],
                    "role_confidence": replay_role_confidence,
                    "skill_weight": float(selected["skill_weight"]),
                    "rating": float(selected["rating"]),
                    "rating_uncertainty": float(selected["rating_uncertainty"]),
                    "conservative_score": float(selected["conservative_score"]),
                    "samples": int(player.get("samples") or 0),
                }
            )

        if not selected_in_replay:
            continue

        selected_replays += 1
        replay_sha256 = path.stem
        raw_path = (
            raw_root
            / replay_sha256[:2]
            / replay_sha256[2:4]
            / f"{replay_sha256}.hbr2"
        )
        bucket = _split_bucket(replay_sha256, split_modulus)
        split = split_names.get(bucket, "train")

        entry = {
            "replay_sha256": replay_sha256,
            "raw_path": str(raw_path),
            "analysis_path": str(path),
            "split_bucket": bucket,
            "total_frames": int(payload.get("totalFrames") or 0),
            "duration_seconds": round(
                int(payload.get("totalFrames") or 0) / 60.0,
                3,
            ),
            "selected_players": sorted(
                selected_in_replay,
                key=lambda row: (row["replay_player_id"], row["identity"]),
            ),
            "selected_player_count": len(selected_in_replay),
        }
        splits[split].append(entry)

    for rows in splits.values():
        rows.sort(key=lambda row: row["replay_sha256"])

    return {
        "schema": ELITE_MANIFEST_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "analysis_version": leaderboard.get("analysis_version", "state-pass-v4"),
        "analysis_root": str(analysis_root),
        "leaderboard_path": str(leaderboard_path),
        "raw_root": str(raw_root),
        "aliases_path": str(aliases_path) if aliases_path else None,
        "selection": {
            "top_fraction_per_role": top_fraction_per_role,
            "min_players_per_role": min_players_per_role,
            "min_matches": min_matches,
            "min_minutes": min_minutes,
            "max_uncertainty": max_uncertainty,
            "split_modulus": split_modulus,
            "validation_buckets": list(validation_buckets),
            "holdout_buckets": list(holdout_buckets),
        },
        "selected_players": selected_players,
        "stats": {
            "analysis_files_scanned": scanned,
            "quality_rejected": rejected,
            "quality_rejection_reasons": dict(sorted(rejection_reasons.items())),
            "elite_source_profile_count": len(selected_players),
            "elite_canonical_player_count": len(
                {row["canonical_identity"] for row in selected_players}
            ),
            "elite_player_count": len(
                {row["canonical_identity"] for row in selected_players}
            ),
            "selected_replays": selected_replays,
            "train_replay_count": len(splits["train"]),
            "validation_replay_count": len(splits["validation"]),
            "holdout_replay_count": len(splits["holdout"]),
            "players_by_role": {
                role: len(
                    {
                        row["canonical_identity"]
                        for row in selected_players
                        if row["role"] == role
                    }
                )
                for role in VALID_ROLES
                if any(row["role"] == role for row in selected_players)
            },
        },
        "train_replays": splits["train"],
        "validation_replays": splits["validation"],
        "holdout_replays": splits["holdout"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-manifest")
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument(
        "--leaderboard",
        type=Path,
        default=Path("/var/lib/haxlab/derived/leaderboards/state-pass-v4.json"),
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("/var/lib/haxlab/raw/replays"),
    )
    parser.add_argument(
        "--aliases",
        type=Path,
        default=Path("/opt/haxlab/configs/player_aliases.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/elite-4v4-manifest.json"
        ),
    )
    parser.add_argument("--top-fraction-per-role", type=float, default=0.20)
    parser.add_argument("--min-players-per-role", type=int, default=4)
    parser.add_argument("--min-matches", type=int, default=30)
    parser.add_argument("--min-minutes", type=float, default=120.0)
    parser.add_argument("--max-uncertainty", type=float, default=1.5)
    parser.add_argument("--split-modulus", type=int, default=20)
    args = parser.parse_args()

    result = build_elite_manifest(
        analysis_root=args.analysis_root,
        leaderboard_path=args.leaderboard,
        raw_root=args.raw_root,
        aliases_path=args.aliases if args.aliases.exists() else None,
        top_fraction_per_role=max(0.01, min(1.0, args.top_fraction_per_role)),
        min_players_per_role=max(1, args.min_players_per_role),
        min_matches=max(1, args.min_matches),
        min_minutes=max(0.0, args.min_minutes),
        max_uncertainty=max(0.0, args.max_uncertainty),
        split_modulus=max(5, args.split_modulus),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["stats"], indent=2, sort_keys=True))
    print("manifest:", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
