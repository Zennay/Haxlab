from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any

MANIFEST_SCHEMA = "haxlab-human-imitation-manifest-v3"
AUDIT_SCHEMA = "haxlab-human-imitation-manifest-audit-v1"
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
_CANONICAL_SHA256 = re.compile(r"^[0-9a-f]{64}$")

TOP_LEVEL_FIELDS = {
    "schema",
    "generated_at",
    "analysis_version",
    "analysis_root",
    "leaderboard_path",
    "leaderboard_sha256",
    "leaderboard_size_bytes",
    "raw_root",
    "selection",
    "selected_players",
    "stats",
    "train_replays",
    "holdout_replays",
}
SELECTION_FIELDS = {
    "top_fraction_per_role",
    "min_players_per_role",
    "min_matches",
    "min_minutes",
    "max_uncertainty",
    "holdout_modulus",
    "holdout_bucket",
}
STATS_FIELDS = {
    "analysis_files_scanned",
    "quality_rejected",
    "quality_rejection_reasons",
    "selected_player_count",
    "train_replay_count",
    "holdout_replay_count",
}
SELECTED_PLAYER_FIELDS = {
    "player_id",
    "name",
    "role",
    "rating",
    "rating_uncertainty",
    "conservative_score",
    "matches",
    "minutes",
}
REPLAY_FIELDS = {
    "replay_sha256",
    "raw_path",
    "analysis_path",
    "analysis_sha256",
    "analysis_size_bytes",
    "total_frames",
    "duration_seconds",
    "selected_player_ids",
    "selected_players",
    "selected_player_count",
    "quality_reasons",
    "example_weight",
}
REPLAY_PLAYER_FIELDS = {"replay_player_id", "identity", "samples"}


class ManifestAuditError(ValueError):
    """Raised when a published training manifest violates its contract."""


def _fail(message: str) -> None:
    raise ManifestAuditError(message)


def _clean_string(value: object, *, field: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        _fail(f"{field} must be a non-empty canonical string")
    return value


def _native_int(value: object, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail(f"{field} must be a native integer >= {minimum}")
    return value


def _finite_number(
    value: object,
    *,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) not in (int, float):
        _fail(f"{field} must be a native finite number")
    number = float(value)
    if not math.isfinite(number):
        _fail(f"{field} must be a native finite number")
    if minimum is not None and number < minimum:
        _fail(f"{field} must be >= {minimum}")
    if maximum is not None and number > maximum:
        _fail(f"{field} must be <= {maximum}")
    return number


def _canonical_sha256(value: object, *, field: str) -> str:
    if type(value) is not str or _CANONICAL_SHA256.fullmatch(value) is None:
        _fail(f"{field} must be a canonical lowercase SHA-256")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    _fail(f"invalid JSON numeric constant: {value}")


def _read_bounded(fd: int) -> bytes:
    chunks: list[bytes] = []
    remaining = MAX_MANIFEST_BYTES + 1
    while remaining > 0:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > MAX_MANIFEST_BYTES:
        _fail(f"manifest exceeds {MAX_MANIFEST_BYTES} byte limit")
    return payload


def _secure_read(path: Path) -> bytes:
    if not isinstance(path, Path):
        _fail("manifest path must be a pathlib.Path")

    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"manifest is not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail("manifest must be a regular non-symlink file")
    if initial.st_size > MAX_MANIFEST_BYTES:
        _fail(f"manifest exceeds {MAX_MANIFEST_BYTES} byte limit")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        _fail("platform lacks required no-follow/non-blocking file primitives")

    try:
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except OSError as exc:
        _fail(f"manifest secure open failed: {exc}")

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("manifest descriptor must reference a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            _fail("manifest identity changed during secure open")

        first = _read_bounded(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_bounded(fd)
        after = os.fstat(fd)

        if first != second:
            _fail("manifest bytes changed during audit read")
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            _fail("manifest file metadata changed during audit read")
        if len(first) != after.st_size:
            _fail("manifest byte count does not match file size")

        try:
            final = path.lstat()
        except OSError as exc:
            _fail(f"manifest path changed during audit read: {exc}")
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            _fail("manifest path changed to an unsafe file type during audit read")
        if (final.st_dev, final.st_ino, final.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            _fail("manifest path identity changed during audit read")
        return first
    except OSError as exc:
        _fail(f"manifest read failed: {exc}")
    finally:
        os.close(fd)


def _load_manifest(payload_bytes: bytes) -> dict[str, Any]:
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"manifest is not valid UTF-8: {exc}")
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"manifest is not valid JSON: {exc}")
    if type(payload) is not dict:
        _fail("manifest root must be a JSON object")
    return payload


def _holdout_bucket(replay_sha256: str, modulus: int) -> int:
    digest = hashlib.sha256(
        f"haxlab-holdout-v1:{replay_sha256}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _validate_selected_player(
    row: object,
    *,
    index: int,
) -> tuple[str, tuple[float, float, int]]:
    prefix = f"selected_players[{index}]"
    if type(row) is not dict or set(row) != SELECTED_PLAYER_FIELDS:
        _fail(f"{prefix} must contain exactly {sorted(SELECTED_PLAYER_FIELDS)}")

    player_id = _clean_string(row["player_id"], field=f"{prefix}.player_id")
    name = row["name"]
    if name is not None:
        _clean_string(name, field=f"{prefix}.name")
    _clean_string(row["role"], field=f"{prefix}.role")

    rating = _finite_number(row["rating"], field=f"{prefix}.rating")
    uncertainty = _finite_number(
        row["rating_uncertainty"],
        field=f"{prefix}.rating_uncertainty",
        minimum=0.0,
    )
    conservative = _finite_number(
        row["conservative_score"],
        field=f"{prefix}.conservative_score",
    )
    if not math.isclose(
        conservative,
        rating - uncertainty,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        _fail(f"{prefix}.conservative_score is inconsistent")

    matches = _native_int(row["matches"], field=f"{prefix}.matches", minimum=0)
    _finite_number(row["minutes"], field=f"{prefix}.minutes", minimum=0.0)
    return player_id, (conservative, rating, matches)


def _validate_replay_player(
    row: object,
    *,
    prefix: str,
) -> tuple[int, str]:
    if type(row) is not dict or set(row) != REPLAY_PLAYER_FIELDS:
        _fail(f"{prefix} must contain exactly {sorted(REPLAY_PLAYER_FIELDS)}")
    replay_player_id = _native_int(
        row["replay_player_id"],
        field=f"{prefix}.replay_player_id",
        minimum=0,
    )
    identity = _clean_string(row["identity"], field=f"{prefix}.identity")
    _native_int(row["samples"], field=f"{prefix}.samples", minimum=1)
    return replay_player_id, identity


def _validate_replay(
    row: object,
    *,
    split: str,
    index: int,
    analysis_root: Path,
    raw_root: Path,
    selected_ids: set[str],
    holdout_modulus: int,
    holdout_bucket: int,
) -> str:
    prefix = f"{split}_replays[{index}]"
    if type(row) is not dict or set(row) != REPLAY_FIELDS:
        _fail(f"{prefix} must contain exactly {sorted(REPLAY_FIELDS)}")

    replay_sha = _canonical_sha256(
        row["replay_sha256"],
        field=f"{prefix}.replay_sha256",
    )
    raw_path = _clean_string(row["raw_path"], field=f"{prefix}.raw_path")
    expected_raw = raw_root / replay_sha[:2] / replay_sha[2:4] / f"{replay_sha}.hbr2"
    if raw_path != str(expected_raw):
        _fail(f"{prefix}.raw_path does not match canonical raw provenance")

    analysis_path_text = _clean_string(
        row["analysis_path"],
        field=f"{prefix}.analysis_path",
    )
    analysis_path = Path(analysis_path_text)
    if analysis_path.name != f"{replay_sha}.json":
        _fail(f"{prefix}.analysis_path filename does not match replay SHA")
    try:
        analysis_path.relative_to(analysis_root)
    except ValueError:
        _fail(f"{prefix}.analysis_path escapes analysis_root")

    _canonical_sha256(row["analysis_sha256"], field=f"{prefix}.analysis_sha256")
    _native_int(
        row["analysis_size_bytes"],
        field=f"{prefix}.analysis_size_bytes",
        minimum=1,
    )
    total_frames = _native_int(
        row["total_frames"],
        field=f"{prefix}.total_frames",
        minimum=1,
    )
    duration = _finite_number(
        row["duration_seconds"],
        field=f"{prefix}.duration_seconds",
        minimum=0.0,
    )
    expected_duration = round(total_frames / 60.0, 3)
    if not math.isclose(duration, expected_duration, rel_tol=0.0, abs_tol=1e-9):
        _fail(f"{prefix}.duration_seconds is inconsistent with total_frames")

    ids = row["selected_player_ids"]
    if type(ids) is not list or not ids:
        _fail(f"{prefix}.selected_player_ids must be a non-empty list")
    canonical_ids = [
        _clean_string(value, field=f"{prefix}.selected_player_ids")
        for value in ids
    ]
    if canonical_ids != sorted(set(canonical_ids)):
        _fail(f"{prefix}.selected_player_ids must be sorted and unique")
    if not set(canonical_ids).issubset(selected_ids):
        _fail(f"{prefix}.selected_player_ids contains an unselected identity")

    players = row["selected_players"]
    if type(players) is not list or not players:
        _fail(f"{prefix}.selected_players must be a non-empty list")
    player_pairs: list[tuple[int, str]] = []
    player_identities: set[str] = set()
    replay_player_ids: set[int] = set()
    for player_index, player in enumerate(players):
        replay_player_id, identity = _validate_replay_player(
            player,
            prefix=f"{prefix}.selected_players[{player_index}]",
        )
        if replay_player_id in replay_player_ids:
            _fail(f"{prefix}.selected_players repeats replay_player_id")
        replay_player_ids.add(replay_player_id)
        player_identities.add(identity)
        player_pairs.append((replay_player_id, identity))
    if player_pairs != sorted(player_pairs):
        _fail(f"{prefix}.selected_players is not canonically ordered")
    if player_identities != set(canonical_ids):
        _fail(f"{prefix}.selected player identity evidence is inconsistent")

    count = _native_int(
        row["selected_player_count"],
        field=f"{prefix}.selected_player_count",
        minimum=1,
    )
    if count != len(players):
        _fail(f"{prefix}.selected_player_count does not match selected_players")

    reasons = row["quality_reasons"]
    if type(reasons) is not list or reasons:
        _fail(f"{prefix}.quality_reasons must be the empty accepted-evidence list")

    _finite_number(
        row["example_weight"],
        field=f"{prefix}.example_weight",
        minimum=0.25,
        maximum=2.0,
    )

    bucket = _holdout_bucket(replay_sha, holdout_modulus)
    if split == "holdout" and bucket != holdout_bucket:
        _fail(f"{prefix} is not assigned to the configured holdout bucket")
    if split == "train" and bucket == holdout_bucket:
        _fail(f"{prefix} belongs in the configured holdout bucket")
    return replay_sha


def _validate_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if set(payload) != TOP_LEVEL_FIELDS:
        _fail(f"manifest must contain exactly {sorted(TOP_LEVEL_FIELDS)}")
    if payload["schema"] != MANIFEST_SCHEMA:
        _fail(f"unsupported manifest schema: {payload['schema']!r}")

    generated_at = _clean_string(payload["generated_at"], field="generated_at")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        _fail("generated_at must be an ISO-8601 timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _fail("generated_at must include a timezone offset")

    analysis_version = _clean_string(
        payload["analysis_version"],
        field="analysis_version",
    )
    if "/" in analysis_version or "\\" in analysis_version:
        _fail("analysis_version must be a single path component")

    analysis_root_text = _clean_string(payload["analysis_root"], field="analysis_root")
    raw_root_text = _clean_string(payload["raw_root"], field="raw_root")
    _clean_string(payload["leaderboard_path"], field="leaderboard_path")
    _canonical_sha256(payload["leaderboard_sha256"], field="leaderboard_sha256")
    _native_int(
        payload["leaderboard_size_bytes"],
        field="leaderboard_size_bytes",
        minimum=1,
    )

    selection = payload["selection"]
    if type(selection) is not dict or set(selection) != SELECTION_FIELDS:
        _fail(f"selection must contain exactly {sorted(SELECTION_FIELDS)}")
    _finite_number(
        selection["top_fraction_per_role"],
        field="selection.top_fraction_per_role",
        minimum=0.0,
        maximum=1.0,
    )
    _native_int(
        selection["min_players_per_role"],
        field="selection.min_players_per_role",
        minimum=1,
    )
    _native_int(selection["min_matches"], field="selection.min_matches", minimum=1)
    _finite_number(
        selection["min_minutes"],
        field="selection.min_minutes",
        minimum=0.0,
    )
    _finite_number(
        selection["max_uncertainty"],
        field="selection.max_uncertainty",
        minimum=0.0,
    )
    holdout_modulus = _native_int(
        selection["holdout_modulus"],
        field="selection.holdout_modulus",
        minimum=2,
    )
    holdout_bucket = _native_int(
        selection["holdout_bucket"],
        field="selection.holdout_bucket",
        minimum=0,
    )
    if holdout_bucket >= holdout_modulus:
        _fail("selection.holdout_bucket must be below holdout_modulus")

    selected_players = payload["selected_players"]
    if type(selected_players) is not list:
        _fail("selected_players must be a JSON array")
    selected_ids: set[str] = set()
    previous_order: tuple[float, float, int] | None = None
    for index, row in enumerate(selected_players):
        player_id, order_key = _validate_selected_player(row, index=index)
        if player_id in selected_ids:
            _fail(f"duplicate selected player_id: {player_id}")
        selected_ids.add(player_id)
        if previous_order is not None and order_key > previous_order:
            _fail("selected_players is not in canonical selection ordering")
        previous_order = order_key

    stats = payload["stats"]
    if type(stats) is not dict or set(stats) != STATS_FIELDS:
        _fail(f"stats must contain exactly {sorted(STATS_FIELDS)}")
    scanned = _native_int(
        stats["analysis_files_scanned"],
        field="stats.analysis_files_scanned",
    )
    rejected = _native_int(
        stats["quality_rejected"],
        field="stats.quality_rejected",
    )
    if rejected > scanned:
        _fail("stats.quality_rejected exceeds analysis_files_scanned")
    reasons = stats["quality_rejection_reasons"]
    if type(reasons) is not dict:
        _fail("stats.quality_rejection_reasons must be a JSON object")
    for reason, count in reasons.items():
        _clean_string(reason, field="stats.quality_rejection_reasons key")
        _native_int(
            count,
            field=f"stats.quality_rejection_reasons.{reason}",
            minimum=1,
        )

    selected_count = _native_int(
        stats["selected_player_count"],
        field="stats.selected_player_count",
    )
    if selected_count != len(selected_players):
        _fail("stats.selected_player_count does not match selected_players")

    train = payload["train_replays"]
    holdout = payload["holdout_replays"]
    if type(train) is not list or type(holdout) is not list:
        _fail("train_replays and holdout_replays must be JSON arrays")
    if _native_int(
        stats["train_replay_count"],
        field="stats.train_replay_count",
    ) != len(train):
        _fail("stats.train_replay_count does not match train_replays")
    if _native_int(
        stats["holdout_replay_count"],
        field="stats.holdout_replay_count",
    ) != len(holdout):
        _fail("stats.holdout_replay_count does not match holdout_replays")
    if len(train) + len(holdout) > scanned:
        _fail("published replay count exceeds analysis_files_scanned")

    analysis_root = Path(analysis_root_text)
    raw_root = Path(raw_root_text)
    seen_replays: set[str] = set()
    for split, rows in (("train", train), ("holdout", holdout)):
        previous_sha: str | None = None
        for index, row in enumerate(rows):
            replay_sha = _validate_replay(
                row,
                split=split,
                index=index,
                analysis_root=analysis_root,
                raw_root=raw_root,
                selected_ids=selected_ids,
                holdout_modulus=holdout_modulus,
                holdout_bucket=holdout_bucket,
            )
            if replay_sha in seen_replays:
                _fail(f"duplicate replay_sha256 across manifest: {replay_sha}")
            seen_replays.add(replay_sha)
            if previous_sha is not None and replay_sha <= previous_sha:
                _fail(f"{split}_replays is not in canonical replay SHA ordering")
            previous_sha = replay_sha

    semantic = {key: value for key, value in payload.items() if key != "generated_at"}
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "analysis_version": analysis_version,
        "selected_player_count": len(selected_players),
        "train_replay_count": len(train),
        "holdout_replay_count": len(holdout),
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def audit_training_manifest(path: Path) -> dict[str, Any]:
    payload_bytes = _secure_read(path)
    payload = _load_manifest(payload_bytes)
    validated = _validate_manifest(payload)
    return {
        "schema": AUDIT_SCHEMA,
        "ok": True,
        "size_bytes": len(payload_bytes),
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        **validated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-training-manifest-audit")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()

    try:
        receipt = audit_training_manifest(args.manifest)
    except ManifestAuditError as exc:
        print(
            json.dumps(
                {"schema": AUDIT_SCHEMA, "ok": False, "error": str(exc)},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2

    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
