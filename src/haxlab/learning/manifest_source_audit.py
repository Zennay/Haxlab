from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any

from haxlab.learning.manifest_audit import (
    ManifestAuditError,
    audit_training_manifest,
)
from haxlab.skill.leaderboard_audit import (
    LeaderboardAuditError,
    audit_leaderboard,
)

AUDIT_SCHEMA = "haxlab-human-imitation-manifest-source-audit-v1"
MAX_LINKED_JSON_BYTES = 64 * 1024 * 1024


class ManifestSourceAuditError(ValueError):
    """Raised when a manifest disagrees with its linked source artifacts."""


def _fail(message: str) -> None:
    raise ManifestSourceAuditError(message)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"duplicate JSON object key in linked source: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    _fail(f"invalid JSON numeric constant in linked source: {value}")


def _read_fd_bounded(fd: int, *, limit: int, label: str) -> bytes:
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        chunk = os.read(fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > limit:
        _fail(f"{label} exceeds {limit} byte limit")
    return payload


def _read_regular(path: Path, *, limit: int, label: str) -> bytes:
    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"{label} is not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail(f"{label} must be a regular non-symlink file")
    if initial.st_size > limit:
        _fail(f"{label} exceeds {limit} byte limit")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        _fail("platform lacks required no-follow/non-blocking file primitives")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except OSError as exc:
        _fail(f"{label} secure open failed: {exc}")

    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            _fail(f"{label} descriptor must reference a regular file")
        if (before.st_dev, before.st_ino) != (initial.st_dev, initial.st_ino):
            _fail(f"{label} identity changed during secure open")

        first = _read_fd_bounded(fd, limit=limit, label=label)
        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_fd_bounded(fd, limit=limit, label=label)
        after = os.fstat(fd)
        if first != second:
            _fail(f"{label} bytes changed during audit read")
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
            _fail(f"{label} metadata changed during audit read")
        if len(first) != after.st_size:
            _fail(f"{label} byte count does not match file size")

        try:
            final = path.lstat()
        except OSError as exc:
            _fail(f"{label} path changed during audit read: {exc}")
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            _fail(f"{label} path changed to an unsafe file type")
        if (final.st_dev, final.st_ino, final.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            _fail(f"{label} path identity changed during audit read")
        return first
    except OSError as exc:
        _fail(f"{label} read failed: {exc}")
    finally:
        os.close(fd)


def _regular_size(path: Path, *, label: str) -> int:
    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"{label} is not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        _fail(f"{label} must be a regular non-symlink file")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        _fail("platform lacks required no-follow/non-blocking file primitives")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except OSError as exc:
        _fail(f"{label} secure open failed: {exc}")
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            _fail(f"{label} descriptor must reference a regular file")
        if (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino):
            _fail(f"{label} identity changed during secure open")
        try:
            final = path.lstat()
        except OSError as exc:
            _fail(f"{label} path changed during secure open: {exc}")
        if stat.S_ISLNK(final.st_mode) or not stat.S_ISREG(final.st_mode):
            _fail(f"{label} path changed to an unsafe file type")
        if (final.st_dev, final.st_ino, final.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            _fail(f"{label} path identity changed during secure open")
        return opened.st_size
    except OSError as exc:
        _fail(f"{label} inspection failed: {exc}")
    finally:
        os.close(fd)


def _open_identity_anchor(
    path: Path,
    *,
    label: str,
) -> tuple[int, tuple[int, int]]:
    try:
        initial = path.lstat()
    except OSError as exc:
        _fail(f"{label} is not readable: {exc}")
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(
        initial.st_mode
    ):
        _fail(f"{label} must be a regular non-symlink file")

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        _fail(
            "platform lacks required "
            "no-follow/non-blocking file primitives"
        )
    try:
        fd = os.open(
            path,
            os.O_RDONLY | nofollow | nonblock,
        )
    except OSError as exc:
        _fail(f"{label} secure open failed: {exc}")

    try:
        try:
            opened = os.fstat(fd)
        except OSError as exc:
            _fail(f"{label} descriptor inspection failed: {exc}")
        if not stat.S_ISREG(opened.st_mode):
            _fail(
                f"{label} descriptor must reference a regular file"
            )
        identity = (opened.st_dev, opened.st_ino)
        if identity != (initial.st_dev, initial.st_ino):
            _fail(
                f"{label} identity changed during secure open"
            )
        _reconfirm_identity_anchor(
            path,
            fd=fd,
            identity=identity,
            label=label,
        )
        return fd, identity
    except ManifestSourceAuditError:
        os.close(fd)
        raise


def _reconfirm_identity_anchor(
    path: Path,
    *,
    fd: int,
    identity: tuple[int, int],
    label: str,
) -> None:
    try:
        opened = os.fstat(fd)
    except OSError as exc:
        _fail(f"{label} descriptor became unreadable: {exc}")
    if not stat.S_ISREG(opened.st_mode):
        _fail(
            f"{label} descriptor no longer references a regular file"
        )
    if (opened.st_dev, opened.st_ino) != identity:
        _fail(f"{label} descriptor identity changed during source audit")

    try:
        current = path.lstat()
    except OSError as exc:
        _fail(f"{label} path changed during source audit: {exc}")
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(
        current.st_mode
    ):
        _fail(
            f"{label} path changed to an unsafe file type "
            "during source audit"
        )
    if (current.st_dev, current.st_ino) != identity:
        _fail(f"{label} path identity changed during source audit")


def _read_identity_anchor(
    path: Path,
    *,
    fd: int,
    identity: tuple[int, int],
    limit: int,
    label: str,
) -> bytes:
    _reconfirm_identity_anchor(
        path,
        fd=fd,
        identity=identity,
        label=label,
    )
    try:
        before = os.fstat(fd)
        if before.st_size > limit:
            _fail(f"{label} exceeds {limit} byte limit")
        os.lseek(fd, 0, os.SEEK_SET)
        first = _read_fd_bounded(
            fd,
            limit=limit,
            label=label,
        )
        os.lseek(fd, 0, os.SEEK_SET)
        second = _read_fd_bounded(
            fd,
            limit=limit,
            label=label,
        )
        after = os.fstat(fd)
    except OSError as exc:
        _fail(f"{label} anchored read failed: {exc}")

    if first != second:
        _fail(f"{label} bytes changed during anchored source read")
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
        _fail(f"{label} metadata changed during anchored source read")
    if (after.st_dev, after.st_ino) != identity:
        _fail(f"{label} descriptor identity changed during source audit")
    if len(first) != after.st_size:
        _fail(f"{label} byte count does not match file size")
    _reconfirm_identity_anchor(
        path,
        fd=fd,
        identity=identity,
        label=label,
    )
    return first


def _load_json(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        _fail(f"{label} is not valid UTF-8: {exc}")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except json.JSONDecodeError as exc:
        _fail(f"{label} is not valid JSON: {exc}")
    if type(value) is not dict:
        _fail(f"{label} root must be a JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _identity_key(player: dict[str, Any]) -> str | None:
    auth_hash = player.get("authHash")
    if auth_hash is not None:
        if type(auth_hash) is not str or not auth_hash.strip():
            return None
        return f"auth:{auth_hash.strip()}"
    name = player.get("name")
    if type(name) is not str:
        return None
    cleaned = " ".join(name.strip().split()).casefold()
    return f"name:{cleaned}" if cleaned else None


def _recompute_selected_players(
    leaderboard_rows: list[dict[str, Any]],
    selection: dict[str, Any],
) -> list[dict[str, Any]]:
    eligible = [
        row
        for row in leaderboard_rows
        if row["matches"] >= selection["min_matches"]
        and float(row["minutes"]) >= float(selection["min_minutes"])
        and float(row["rating_uncertainty"]) <= float(selection["max_uncertainty"])
    ]
    by_role: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible:
        by_role[row["role"].strip()].append(row)

    selected: list[dict[str, Any]] = []
    for role, rows in sorted(by_role.items()):
        rows.sort(
            key=lambda row: (
                float(row["rating"]) - float(row["rating_uncertainty"]),
                float(row["rating"]),
                int(row["matches"]),
            ),
            reverse=True,
        )
        count = max(
            selection["min_players_per_role"],
            math.ceil(
                len(rows) * float(selection["top_fraction_per_role"])
            ),
        )
        for row in rows[: min(len(rows), count)]:
            selected.append(
                {
                    "player_id": row["player_id"],
                    "name": row.get("name"),
                    "role": role,
                    "rating": float(row["rating"]),
                    "rating_uncertainty": float(row["rating_uncertainty"]),
                    "conservative_score": float(row["rating"])
                    - float(row["rating_uncertainty"]),
                    "matches": int(row["matches"]),
                    "minutes": float(row["minutes"]),
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


def _validate_analysis_quality(payload: dict[str, Any], *, label: str) -> None:
    schema_version = payload.get("schemaVersion")
    if type(schema_version) is not int or schema_version < 4:
        _fail(f"{label} does not satisfy schemaVersion >= 4")

    total_frames = payload.get("totalFrames")
    if type(total_frames) is not int or total_frames < 7200:
        _fail(f"{label} does not satisfy the two-minute frame floor")

    simulation = payload.get("simulation")
    if type(simulation) is not dict:
        _fail(f"{label} has invalid simulation evidence")
    sampled = simulation.get("sampledStateCount")
    if type(sampled) is not int or sampled <= 0:
        _fail(f"{label} has no sampled-state evidence")

    players = payload.get("players")
    if type(players) is not list or len(players) < 4 or any(
        type(player) is not dict for player in players
    ):
        _fail(f"{label} has invalid player evidence")

    feature_summary = payload.get("featureSummary")
    if type(feature_summary) is not dict:
        _fail(f"{label} has invalid feature-summary evidence")
    touches = feature_summary.get("touches")
    if type(touches) is not int or touches <= 0:
        _fail(f"{label} has no touch evidence")


def _expected_replay_players(
    payload: dict[str, Any],
    *,
    selected_ids: set[str],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
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
            selected.append(
                {
                    "replay_player_id": replay_player_id,
                    "identity": identity,
                    "samples": samples,
                }
            )
    replay_ids = [row["replay_player_id"] for row in selected]
    if len(replay_ids) != len(set(replay_ids)):
        _fail("linked analysis contains duplicate selected replay_player_id evidence")
    selected.sort(key=lambda row: (row["replay_player_id"], row["identity"]))
    return selected


def audit_manifest_sources(manifest_path: Path) -> dict[str, Any]:
    manifest_fd: int | None = None
    leaderboard_fd: int | None = None

    try:
        manifest_fd, manifest_identity = (
            _open_identity_anchor(
                manifest_path,
                label="manifest",
            )
        )
        try:
            manifest_receipt = audit_training_manifest(
                manifest_path
            )
        except ManifestAuditError as exc:
            _fail(f"structural manifest audit failed: {exc}")

        manifest_bytes = _read_identity_anchor(
            manifest_path,
            fd=manifest_fd,
            identity=manifest_identity,
            limit=MAX_LINKED_JSON_BYTES,
            label="manifest",
        )

        manifest_sha = hashlib.sha256(
            manifest_bytes
        ).hexdigest()
        if (
            manifest_sha != manifest_receipt["sha256"]
            or len(manifest_bytes)
            != manifest_receipt["size_bytes"]
        ):
            _fail(
                "manifest changed between structural "
                "and source audit"
            )
        manifest = _load_json(
            manifest_bytes,
            label="manifest",
        )

        leaderboard_path = Path(
            manifest["leaderboard_path"]
        )
        leaderboard_fd, leaderboard_identity = (
            _open_identity_anchor(
                leaderboard_path,
                label="linked leaderboard",
            )
        )
        try:
            leaderboard_receipt = audit_leaderboard(
                leaderboard_path
            )
        except LeaderboardAuditError as exc:
            _fail(
                f"linked leaderboard audit failed: {exc}"
            )

        leaderboard_bytes = _read_identity_anchor(
            leaderboard_path,
            fd=leaderboard_fd,
            identity=leaderboard_identity,
            limit=MAX_LINKED_JSON_BYTES,
            label="linked leaderboard",
        )

        leaderboard_sha = hashlib.sha256(
            leaderboard_bytes
        ).hexdigest()
        if (
            leaderboard_sha
            != manifest["leaderboard_sha256"]
        ):
            _fail(
                "linked leaderboard SHA-256 does not "
                "match manifest"
            )
        if (
            len(leaderboard_bytes)
            != manifest["leaderboard_size_bytes"]
        ):
            _fail(
                "linked leaderboard byte size does not "
                "match manifest"
            )
        if (
            leaderboard_receipt["sha256"]
            != leaderboard_sha
        ):
            _fail(
                "linked leaderboard changed during source audit"
            )

        leaderboard = _load_json(
            leaderboard_bytes,
            label="linked leaderboard",
        )
        if (
            leaderboard.get("analysis_version")
            != manifest["analysis_version"]
        ):
            _fail(
                "linked leaderboard analysis_version "
                "does not match manifest"
            )
        if str(Path(leaderboard["source_root"])) != str(
            Path(manifest["analysis_root"])
        ):
            _fail(
                "linked leaderboard source_root does not "
                "match manifest analysis_root"
            )

        expected_selected = _recompute_selected_players(
            leaderboard["rows"],
            manifest["selection"],
        )
        if _canonical(expected_selected) != _canonical(
            manifest["selected_players"]
        ):
            _fail(
                "manifest selected_players does not match "
                "linked leaderboard policy"
            )

        selected_ids = {
            row["player_id"]
            for row in manifest["selected_players"]
        }
        inventory: list[dict[str, Any]] = [
            {
                "kind": "leaderboard",
                "path": str(leaderboard_path),
                "size_bytes": len(leaderboard_bytes),
                "sha256": leaderboard_sha,
            }
        ]

        replay_count = 0
        raw_count = 0
        for split in (
            "train_replays",
            "holdout_replays",
        ):
            for index, row in enumerate(manifest[split]):
                replay_count += 1
                label = f"{split}[{index}]"
                analysis_path = Path(
                    row["analysis_path"]
                )
                analysis_bytes = _read_regular(
                    analysis_path,
                    limit=MAX_LINKED_JSON_BYTES,
                    label=f"{label} analysis",
                )
                analysis_sha = hashlib.sha256(
                    analysis_bytes
                ).hexdigest()
                if (
                    analysis_sha
                    != row["analysis_sha256"]
                ):
                    _fail(
                        f"{label} analysis SHA-256 "
                        "does not match manifest"
                    )
                if (
                    len(analysis_bytes)
                    != row["analysis_size_bytes"]
                ):
                    _fail(
                        f"{label} analysis byte size "
                        "does not match manifest"
                    )

                analysis = _load_json(
                    analysis_bytes,
                    label=f"{label} analysis",
                )
                _validate_analysis_quality(
                    analysis,
                    label=f"{label} analysis",
                )
                if (
                    analysis["totalFrames"]
                    != row["total_frames"]
                ):
                    _fail(
                        f"{label} total_frames does not "
                        "match linked analysis"
                    )

                expected_players = (
                    _expected_replay_players(
                        analysis,
                        selected_ids=selected_ids,
                    )
                )
                if _canonical(
                    expected_players
                ) != _canonical(
                    row["selected_players"]
                ):
                    _fail(
                        f"{label} selected_players does "
                        "not match linked analysis evidence"
                    )

                raw_path = Path(row["raw_path"])
                raw_size = _regular_size(
                    raw_path,
                    label=f"{label} raw replay",
                )
                raw_count += 1

                inventory.append(
                    {
                        "kind": "analysis",
                        "replay_sha256": (
                            row["replay_sha256"]
                        ),
                        "path": str(analysis_path),
                        "size_bytes": len(
                            analysis_bytes
                        ),
                        "sha256": analysis_sha,
                    }
                )
                inventory.append(
                    {
                        "kind": "raw-existence",
                        "replay_sha256": (
                            row["replay_sha256"]
                        ),
                        "path": str(raw_path),
                        "size_bytes": raw_size,
                    }
                )

        inventory_bytes = _canonical(inventory)
        return {
            "schema": AUDIT_SCHEMA,
            "ok": True,
            "manifest_sha256": manifest_sha,
            "leaderboard_sha256": leaderboard_sha,
            "analysis_artifact_count": replay_count,
            "raw_artifact_count": raw_count,
            "source_inventory_sha256": hashlib.sha256(
                inventory_bytes
            ).hexdigest(),
        }
    finally:
        if leaderboard_fd is not None:
            os.close(leaderboard_fd)
        if manifest_fd is not None:
            os.close(manifest_fd)


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-training-manifest-source-audit")
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        receipt = audit_manifest_sources(args.manifest)
    except ManifestSourceAuditError as exc:
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
