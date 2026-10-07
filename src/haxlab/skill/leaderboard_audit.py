from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any

LEADERBOARD_SCHEMA = "haxlab-skill-leaderboard-v1"
AUDIT_SCHEMA = "haxlab-skill-leaderboard-audit-v1"
MAX_LEADERBOARD_BYTES = 8 * 1024 * 1024
DIMENSIONS = (
    "retention",
    "progression",
    "creation",
    "finishing",
    "defending",
    "positioning",
    "pressure_recovery",
    "risk_management",
)
ROW_FIELDS = {
    "player_id",
    "name",
    "matches",
    "minutes",
    "role",
    "rating",
    "rating_uncertainty",
    "overall_z",
    "average_teammate_context",
    "average_opponent_context",
    "dimensions",
}
DIMENSION_FIELDS = {"mean", "uncertainty", "effective_weight"}
TOP_LEVEL_REQUIRED = {
    "schema",
    "generated_at",
    "source_root",
    "min_matches",
    "min_minutes",
    "rows",
}
TOP_LEVEL_OPTIONAL = {"analysis_version"}
ROLES = {"defender", "midfield", "forward", "unknown"}


class LeaderboardAuditError(ValueError):
    """Raised when published leaderboard evidence violates its contract."""


def _fail(message: str) -> None:
    raise LeaderboardAuditError(message)


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
    remaining = MAX_LEADERBOARD_BYTES + 1
    while remaining > 0:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)

    payload = b"".join(chunks)
    if len(payload) > MAX_LEADERBOARD_BYTES:
        _fail(f"leaderboard exceeds {MAX_LEADERBOARD_BYTES} byte limit")
    return payload


def _secure_read(path: Path) -> bytes:
    if not isinstance(path, Path):
        _fail("leaderboard path must be a pathlib.Path")

    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"leaderboard is not readable: {exc}")

    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail("leaderboard must be a regular non-symlink file")
    if initial.st_size > MAX_LEADERBOARD_BYTES:
        _fail(f"leaderboard exceeds {MAX_LEADERBOARD_BYTES} byte limit")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        _fail("platform lacks required no-follow/non-blocking file primitives")

    flags = os.O_RDONLY | nofollow | nonblock
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        _fail(f"leaderboard secure open failed: {exc}")

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail("leaderboard descriptor must reference a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            _fail("leaderboard identity changed during secure open")
        if before.st_size > MAX_LEADERBOARD_BYTES:
            _fail(f"leaderboard exceeds {MAX_LEADERBOARD_BYTES} byte limit")

        first = _read_bounded(fd)

        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_bounded(fd)
        after = os.fstat(fd)
        if first != second:
            _fail("leaderboard bytes changed during audit read")
        if (
            (before.st_dev, before.st_ino, before.st_size)
            != (after.st_dev, after.st_ino, after.st_size)
        ):
            _fail("leaderboard file metadata changed during audit read")
        if len(first) != after.st_size:
            _fail("leaderboard byte count does not match file size")
        return first
    except OSError as exc:
        _fail(f"leaderboard read failed: {exc}")
    finally:
        os.close(fd)


def _load_snapshot(payload_bytes: bytes) -> dict[str, Any]:
    try:
        text = payload_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"leaderboard is not valid UTF-8: {exc}")

    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"leaderboard is not valid JSON: {exc}")

    if type(payload) is not dict:
        _fail("leaderboard root must be a JSON object")
    return payload


def _validate_dimension(
    dimension: object,
    *,
    row_index: int,
    name: str,
) -> None:
    prefix = f"rows[{row_index}].dimensions.{name}"
    if type(dimension) is not dict or set(dimension) != DIMENSION_FIELDS:
        _fail(f"{prefix} must contain exactly {sorted(DIMENSION_FIELDS)}")

    _finite_number(
        dimension["mean"],
        field=f"{prefix}.mean",
        minimum=-3.25,
        maximum=3.25,
    )
    _finite_number(
        dimension["uncertainty"],
        field=f"{prefix}.uncertainty",
        minimum=0.0,
    )
    _finite_number(
        dimension["effective_weight"],
        field=f"{prefix}.effective_weight",
        minimum=0.0,
    )


def _validate_row(
    row: object,
    *,
    row_index: int,
    min_matches: int,
    min_minutes: float,
) -> tuple[str, tuple[float, float, int]]:
    prefix = f"rows[{row_index}]"
    if type(row) is not dict or set(row) != ROW_FIELDS:
        _fail(f"{prefix} must contain exactly {sorted(ROW_FIELDS)}")

    player_id = _clean_string(row["player_id"], field=f"{prefix}.player_id")
    _clean_string(row["name"], field=f"{prefix}.name")
    role = _clean_string(row["role"], field=f"{prefix}.role")
    if role not in ROLES:
        _fail(f"{prefix}.role is not canonical")

    matches = _native_int(row["matches"], field=f"{prefix}.matches", minimum=1)
    if matches < min_matches:
        _fail(f"{prefix}.matches is below snapshot min_matches")

    minutes = _finite_number(
        row["minutes"],
        field=f"{prefix}.minutes",
        minimum=0.0,
    )
    if minutes < min_minutes:
        _fail(f"{prefix}.minutes is below snapshot min_minutes")

    rating = _finite_number(
        row["rating"],
        field=f"{prefix}.rating",
        minimum=20.0,
        maximum=80.0,
    )
    rating_uncertainty = _finite_number(
        row["rating_uncertainty"],
        field=f"{prefix}.rating_uncertainty",
        minimum=0.0,
    )
    overall_z = _finite_number(
        row["overall_z"],
        field=f"{prefix}.overall_z",
        minimum=-3.25,
        maximum=3.25,
    )
    _finite_number(
        row["average_teammate_context"],
        field=f"{prefix}.average_teammate_context",
        minimum=-1.0,
        maximum=1.0,
    )
    _finite_number(
        row["average_opponent_context"],
        field=f"{prefix}.average_opponent_context",
        minimum=-1.0,
        maximum=1.0,
    )

    expected_rating = max(20.0, min(80.0, 50.0 + 10.0 * overall_z))
    if not math.isclose(rating, expected_rating, rel_tol=0.0, abs_tol=1e-9):
        _fail(f"{prefix}.rating is inconsistent with overall_z")

    dimensions = row["dimensions"]
    if type(dimensions) is not dict or set(dimensions) != set(DIMENSIONS):
        _fail(f"{prefix}.dimensions must contain exactly {list(DIMENSIONS)}")
    for name in DIMENSIONS:
        _validate_dimension(dimensions[name], row_index=row_index, name=name)

    return player_id, (rating, -rating_uncertainty, matches)


def _validate_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    keys = set(payload)
    if not TOP_LEVEL_REQUIRED.issubset(keys):
        missing = sorted(TOP_LEVEL_REQUIRED - keys)
        _fail(f"leaderboard is missing required fields: {missing}")
    if not keys.issubset(TOP_LEVEL_REQUIRED | TOP_LEVEL_OPTIONAL):
        extra = sorted(keys - TOP_LEVEL_REQUIRED - TOP_LEVEL_OPTIONAL)
        _fail(f"leaderboard contains unsupported fields: {extra}")

    if payload["schema"] != LEADERBOARD_SCHEMA:
        _fail(f"unsupported leaderboard schema: {payload['schema']!r}")

    generated_at = _clean_string(payload["generated_at"], field="generated_at")
    try:
        timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError:
        _fail("generated_at must be an ISO-8601 timestamp")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        _fail("generated_at must include a timezone offset")

    source_root = _clean_string(payload["source_root"], field="source_root")
    min_matches = _native_int(payload["min_matches"], field="min_matches", minimum=1)
    min_minutes = _finite_number(
        payload["min_minutes"],
        field="min_minutes",
        minimum=0.0,
    )

    analysis_version = payload.get("analysis_version")
    if analysis_version is not None:
        analysis_version = _clean_string(
            analysis_version,
            field="analysis_version",
        )
        if "/" in analysis_version or "\\" in analysis_version:
            _fail("analysis_version must be a single path component")

    rows = payload["rows"]
    if type(rows) is not list:
        _fail("rows must be a JSON array")

    seen_players: set[str] = set()
    previous_order: tuple[float, float, int] | None = None
    for index, row in enumerate(rows):
        player_id, order_key = _validate_row(
            row,
            row_index=index,
            min_matches=min_matches,
            min_minutes=min_minutes,
        )
        if player_id in seen_players:
            _fail(f"duplicate player_id: {player_id}")
        seen_players.add(player_id)

        if previous_order is not None and order_key > previous_order:
            _fail("leaderboard rows are not in canonical rating ordering")
        previous_order = order_key

    semantic_inventory = {
        "schema": LEADERBOARD_SCHEMA,
        "analysis_version": analysis_version,
        "source_root": source_root,
        "min_matches": min_matches,
        "min_minutes": min_minutes,
        "rows": rows,
    }
    canonical = json.dumps(
        semantic_inventory,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "row_count": len(rows),
        "analysis_version": analysis_version,
        "inventory_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def audit_leaderboard(path: Path) -> dict[str, Any]:
    payload_bytes = _secure_read(path)
    payload = _load_snapshot(payload_bytes)
    validated = _validate_snapshot(payload)
    return {
        "schema": AUDIT_SCHEMA,
        "ok": True,
        "size_bytes": len(payload_bytes),
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        **validated,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-skill-leaderboard-audit")
    parser.add_argument("leaderboard", type=Path)
    args = parser.parse_args()

    try:
        receipt = audit_leaderboard(args.leaderboard)
    except LeaderboardAuditError as exc:
        print(
            json.dumps(
                {
                    "schema": AUDIT_SCHEMA,
                    "ok": False,
                    "error": str(exc),
                },
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
