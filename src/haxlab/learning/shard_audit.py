from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, BinaryIO

AUDIT_SCHEMA = "haxlab-imitation-shard-audit-v1"
INDEX_SCHEMA = "haxlab-imitation-shard-index-v2"
MANIFEST_SCHEMA = "haxlab-human-imitation-manifest-v3"
META_SCHEMA = "haxlab-imitation-extract-summary-v2"
SHARD_SCHEMA = "haxlab-imitation-shard-v2"
_CANONICAL_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_JSON_BYTES = 32 * 1024 * 1024
EXPECTED_COLUMNS = (
    "frame",
    "player_index",
    "team_id",
    "own_x",
    "own_y",
    "own_vx",
    "own_vy",
    "ball_dx",
    "ball_dy",
    "ball_dvx",
    "ball_dvy",
    "tm1_dx",
    "tm1_dy",
    "tm1_dvx",
    "tm1_dvy",
    "tm1_present",
    "tm2_dx",
    "tm2_dy",
    "tm2_dvx",
    "tm2_dvy",
    "tm2_present",
    "op1_dx",
    "op1_dy",
    "op1_dvx",
    "op1_dvy",
    "op1_present",
    "op2_dx",
    "op2_dy",
    "op2_dvx",
    "op2_dvy",
    "op2_present",
    "op3_dx",
    "op3_dy",
    "op3_dvx",
    "op3_dvy",
    "op3_present",
    "dir_x",
    "dir_y",
    "kick",
)
EXPECTED_ROW_WIDTH = len(EXPECTED_COLUMNS)
_FLOAT32_BYTES = 4


class AuditInputError(ValueError):
    """Raised when the audit root/index cannot be read safely."""


def _unique_object(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AuditInputError(
                f"duplicate_json_key:{key}"
            )
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise AuditInputError(
        f"invalid_json_constant:{value}"
    )


def _native_int(value: Any, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _canonical_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(_CANONICAL_SHA256.fullmatch(value))


def _open_regular(path: Path) -> BinaryIO:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise AuditInputError(
            f"unsafe_or_unreadable_file:{path.name}:{exc}"
        ) from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise AuditInputError(f"not_regular_file:{path.name}")
        return os.fdopen(fd, "rb", closefd=True)
    except Exception:
        os.close(fd)
        raise


def _read_regular_bytes(
    path: Path,
    *,
    max_bytes: int = _MAX_JSON_BYTES,
) -> bytes:
    with _open_regular(path) as handle:
        payload = handle.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise AuditInputError(f"file_too_large:{path.name}")
    return payload


def _load_object(path: Path) -> tuple[dict[str, Any], bytes]:
    payload = _read_regular_bytes(path)
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditInputError(f"invalid_json:{path.name}:{exc}") from exc
    if not isinstance(value, dict):
        raise AuditInputError(f"json_not_object:{path.name}")
    return value, payload


def _validate_identity_map(
    value: Any,
    *,
    key_mode: str,
) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(value, dict):
        return None, "not_object"
    result: dict[str, str] = {}
    for key, identity in value.items():
        if (
            not isinstance(key, str)
            or not isinstance(identity, str)
            or not identity.strip()
        ):
            return None, "invalid_entry"
        if key_mode == "dense_index":
            if not key.isdigit():
                return None, "invalid_index_key"
        elif key_mode == "player_id":
            if not key.isdigit() or int(key) < 0:
                return None, "invalid_player_id_key"
        else:  # pragma: no cover - internal contract
            raise AssertionError(key_mode)
        result[key] = identity
    if key_mode == "dense_index":
        if set(result) != {str(index) for index in range(len(result))}:
            return None, "non_dense_indices"
        if len(set(result.values())) != len(result):
            return None, "duplicate_identity"
    return result, None


def _validate_selected_player_rows(
    value: Any,
) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(value, list):
        return None, "not_list"
    result: dict[str, str] = {}
    for row in value:
        if not isinstance(row, dict):
            return None, "invalid_row"
        replay_player_id = row.get("replay_player_id")
        identity = row.get("identity")
        samples = row.get("samples")
        if (
            not _native_int(replay_player_id)
            or not isinstance(identity, str)
            or not identity.strip()
            or not _native_int(samples, minimum=1)
        ):
            return None, "invalid_row"
        key = str(replay_player_id)
        if key in result:
            return None, "duplicate_replay_player_id"
        result[key] = identity
    return result, None


def _inspect_gzip_shard(
    path: Path,
    *,
    expected_uncompressed_bytes: int,
) -> tuple[int, int, str]:
    with _open_regular(path) as handle:
        digest = hashlib.sha256()
        compressed_bytes = 0
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            compressed_bytes += len(chunk)

        handle.seek(0)
        uncompressed_bytes = 0
        try:
            with gzip.GzipFile(fileobj=handle, mode="rb") as decompressed:
                while True:
                    chunk = decompressed.read(1024 * 1024)
                    if not chunk:
                        break
                    uncompressed_bytes += len(chunk)
                    if uncompressed_bytes > expected_uncompressed_bytes:
                        raise AuditInputError(
                            "uncompressed_size_exceeds_metadata:"
                            f"{path.name}"
                        )
        except (OSError, EOFError) as exc:
            raise AuditInputError(
                f"invalid_gzip:{path.name}:{exc}"
            ) from exc

    if uncompressed_bytes != expected_uncompressed_bytes:
        raise AuditInputError(
            f"uncompressed_size_mismatch:{path.name}:"
            f"{uncompressed_bytes}!={expected_uncompressed_bytes}"
        )
    return compressed_bytes, uncompressed_bytes, digest.hexdigest()


def _validate_index_header(
    index: dict[str, Any],
    errors: list[str],
) -> None:
    if index.get("schema") != INDEX_SCHEMA:
        errors.append(f"index_schema:{index.get('schema')!r}")
    if index.get("manifest_schema") != MANIFEST_SCHEMA:
        errors.append(f"manifest_schema:{index.get('manifest_schema')!r}")
    if index.get("split") not in {"train", "holdout"}:
        errors.append(f"split:{index.get('split')!r}")
    if (
        not isinstance(index.get("analysis_version"), str)
        or not index["analysis_version"].strip()
    ):
        errors.append("analysis_version:invalid")
    if (
        not isinstance(index.get("manifest_path"), str)
        or not index["manifest_path"].strip()
    ):
        errors.append("manifest_path:invalid")
    if not _native_int(index.get("sample_every_ticks"), minimum=1):
        errors.append("sample_every_ticks:invalid")
    for key in (
        "requested_replays",
        "successful_replays",
        "failed_replays",
        "samples",
        "compressed_bytes",
        "selected_players_seen",
        "unknown_input_samples_skipped",
    ):
        if not _native_int(index.get(key)):
            errors.append(f"{key}:invalid")
    if not isinstance(index.get("entries"), list):
        errors.append("entries:not_list")
    if not isinstance(index.get("failures"), list):
        errors.append("failures:not_list")


def _validate_meta(
    sha256: str,
    meta: dict[str, Any],
    entry: dict[str, Any],
    *,
    sample_every_ticks: int,
    errors: list[str],
) -> tuple[int, int, int, int] | None:
    prefix = f"{sha256}:"
    if meta.get("schema") != META_SCHEMA:
        errors.append(prefix + "meta_schema")
    if meta.get("shardSchema") != SHARD_SCHEMA:
        errors.append(prefix + "shard_schema")
    if meta.get("format") != "float32-le-gzip":
        errors.append(prefix + "format")
    if meta.get("dtype") != "float32-le":
        errors.append(prefix + "dtype")
    if meta.get("canonicalAttackDirection") != "+x":
        errors.append(prefix + "attack_direction")
    if meta.get("rowWidth") != EXPECTED_ROW_WIDTH:
        errors.append(prefix + "row_width")
    if meta.get("columns") != list(EXPECTED_COLUMNS):
        errors.append(prefix + "columns")
    if meta.get("replay_sha256") != sha256:
        errors.append(prefix + "meta_replay_sha256")
    if meta.get("sourceFile") != f"{sha256}.hbr2":
        errors.append(prefix + "source_file")
    output_path = meta.get("outputPath")
    if (
        not isinstance(output_path, str)
        or Path(output_path).name != f"{sha256}.f32.gz"
    ):
        errors.append(prefix + "output_path")
    if meta.get("status") != "ok":
        errors.append(prefix + "meta_status")
    if meta.get("sampleEveryTicks") != sample_every_ticks:
        errors.append(prefix + "sample_cadence")

    for key in ("samples", "selectedStateSamples", "skippedUnknownInput"):
        if not _native_int(meta.get(key)):
            errors.append(prefix + key)
    for key in ("selectedPlayersRequested", "selectedPlayersSeen"):
        if not _native_int(meta.get(key)):
            errors.append(prefix + key)
    for key in ("totalFrames", "framesAdvanced", "compressedBytes"):
        if not _native_int(meta.get(key)):
            errors.append(prefix + key)

    samples = meta.get("samples")
    selected_state_samples = meta.get("selectedStateSamples")
    skipped = meta.get("skippedUnknownInput")
    requested = meta.get("selectedPlayersRequested")
    seen = meta.get("selectedPlayersSeen")
    total_frames = meta.get("totalFrames")
    frames_advanced = meta.get("framesAdvanced")
    compressed_bytes = meta.get("compressedBytes")

    if _native_int(samples) and samples <= 0:
        errors.append(prefix + "samples_nonpositive")
    if (
        _native_int(samples)
        and _native_int(selected_state_samples)
        and _native_int(skipped)
        and selected_state_samples != samples + skipped
    ):
        errors.append(prefix + "sample_accounting")
    if _native_int(requested) and _native_int(seen) and seen > requested:
        errors.append(
            prefix + "selected_players_seen_exceeds_requested"
        )
    if (
        _native_int(total_frames)
        and _native_int(frames_advanced)
        and total_frames > 0
        and frames_advanced < total_frames - 1
    ):
        errors.append(prefix + "incomplete_reconstruction")
    if _native_int(compressed_bytes) and compressed_bytes <= 0:
        errors.append(prefix + "compressed_bytes_nonpositive")

    selected_players_map, selected_players_error = _validate_identity_map(
        meta.get("selectedPlayers"),
        key_mode="dense_index",
    )
    if selected_players_error:
        errors.append(
            prefix + f"selectedPlayers_{selected_players_error}"
        )
    selected_replay_map, selected_replay_error = _validate_identity_map(
        meta.get("selectedReplayPlayers"),
        key_mode="player_id",
    )
    if selected_replay_error:
        errors.append(
            prefix + f"selectedReplayPlayers_{selected_replay_error}"
        )
    selected_rows_map, selected_rows_error = _validate_selected_player_rows(
        meta.get("selected_players")
    )
    if selected_rows_error:
        errors.append(
            prefix + f"selected_players_{selected_rows_error}"
        )

    if selected_replay_map is not None:
        if (
            _native_int(requested)
            and requested != len(selected_replay_map)
        ):
            errors.append(prefix + "selected_players_requested_count")
        identities = sorted(set(selected_replay_map.values()))
        if meta.get("selected_player_ids") != identities:
            errors.append(prefix + "selected_player_ids")
        if (
            selected_players_map is not None
            and sorted(selected_players_map.values()) != identities
        ):
            errors.append(prefix + "selectedPlayers_identity_set")
        if (
            selected_rows_map is not None
            and selected_rows_map != selected_replay_map
        ):
            errors.append(prefix + "selected_players_mapping")

    for key, value in meta.items():
        if key == "status":
            continue
        if entry.get(key) != value:
            errors.append(prefix + f"index_meta_mismatch:{key}")
    if entry.get("status") not in {"ok", "cached"}:
        errors.append(prefix + "index_status")

    if not (
        _native_int(samples, minimum=1)
        and _native_int(compressed_bytes, minimum=1)
        and _native_int(seen)
        and _native_int(skipped)
    ):
        return None
    return samples, compressed_bytes, seen, skipped


def audit_shard_directory(shard_dir: Path) -> dict[str, Any]:
    try:
        root_stat = os.stat(shard_dir, follow_symlinks=False)
    except OSError as exc:
        raise AuditInputError(
            f"unsafe_or_missing_shard_root:{exc}"
        ) from exc
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or shard_dir.is_symlink()
    ):
        raise AuditInputError(
            "shard_root_must_be_regular_directory"
        )

    index_path = shard_dir / "_index.json"
    index, index_bytes_before = _load_object(index_path)
    index_sha256 = hashlib.sha256(index_bytes_before).hexdigest()
    errors: list[str] = []
    _validate_index_header(index, errors)

    entries_value = index.get("entries")
    failures_value = index.get("failures")
    entries = entries_value if isinstance(entries_value, list) else []
    failures = failures_value if isinstance(failures_value, list) else []

    entry_by_sha: dict[str, dict[str, Any]] = {}
    for position, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entry_{position}:not_object")
            continue
        replay_sha = entry.get("replay_sha256")
        if not _canonical_sha(replay_sha):
            errors.append(
                f"entry_{position}:invalid_replay_sha256"
            )
            continue
        if replay_sha in entry_by_sha:
            errors.append(
                f"{replay_sha}:duplicate_index_entry"
            )
            continue
        entry_by_sha[replay_sha] = entry

    failure_shas: set[str] = set()
    for position, failure in enumerate(failures):
        if not isinstance(failure, dict):
            errors.append(f"failure_{position}:not_object")
            continue
        replay_sha = failure.get("replay_sha256")
        error = failure.get("error")
        if not _canonical_sha(replay_sha):
            errors.append(
                f"failure_{position}:invalid_replay_sha256"
            )
            continue
        if replay_sha in failure_shas:
            errors.append(f"{replay_sha}:duplicate_failure")
        failure_shas.add(replay_sha)
        if not isinstance(error, str) or not error.strip():
            errors.append(
                f"{replay_sha}:missing_failure_error"
            )

    for replay_sha in sorted(set(entry_by_sha) & failure_shas):
        errors.append(f"{replay_sha}:both_success_and_failure")

    expected_files = {"_index.json"}
    inventory_lines: list[str] = []
    aggregate_samples = 0
    aggregate_compressed = 0
    aggregate_seen = 0
    aggregate_skipped = 0
    aggregate_uncompressed = 0
    sample_every_ticks = index.get("sample_every_ticks")
    cadence = (
        sample_every_ticks
        if _native_int(sample_every_ticks, minimum=1)
        else -1
    )

    for replay_sha in sorted(entry_by_sha):
        entry = entry_by_sha[replay_sha]
        meta_name = f"{replay_sha}.meta.json"
        shard_name = f"{replay_sha}.f32.gz"
        expected_files.update({meta_name, shard_name})
        meta_path = shard_dir / meta_name
        shard_path = shard_dir / shard_name
        try:
            meta, _ = _load_object(meta_path)
        except AuditInputError as exc:
            errors.append(f"{replay_sha}:meta:{exc}")
            continue

        validated = _validate_meta(
            replay_sha,
            meta,
            entry,
            sample_every_ticks=cadence,
            errors=errors,
        )
        if validated is None:
            continue
        samples, declared_compressed, seen, skipped = validated
        expected_uncompressed = (
            samples * EXPECTED_ROW_WIDTH * _FLOAT32_BYTES
        )
        try:
            (
                actual_compressed,
                actual_uncompressed,
                compressed_sha,
            ) = _inspect_gzip_shard(
                shard_path,
                expected_uncompressed_bytes=expected_uncompressed,
            )
        except AuditInputError as exc:
            errors.append(f"{replay_sha}:shard:{exc}")
            continue

        if actual_compressed != declared_compressed:
            errors.append(
                f"{replay_sha}:compressed_size:"
                f"{actual_compressed}!={declared_compressed}"
            )
        aggregate_samples += samples
        aggregate_compressed += actual_compressed
        aggregate_seen += seen
        aggregate_skipped += skipped
        aggregate_uncompressed += actual_uncompressed
        inventory_lines.append(
            "\t".join(
                (
                    replay_sha,
                    compressed_sha,
                    str(actual_compressed),
                    str(actual_uncompressed),
                    str(samples),
                )
            )
            + "\n"
        )

    try:
        present = list(os.scandir(shard_dir))
    except OSError as exc:
        raise AuditInputError(
            f"cannot_list_shard_root:{exc}"
        ) from exc
    for item in present:
        name = item.name
        if item.is_symlink():
            errors.append(f"symlink_artifact:{name}")
            continue
        if (
            name.endswith(".f32.gz")
            or name.endswith(".meta.json")
            or ".f32.gz.tmp-" in name
        ) and name not in expected_files:
            errors.append(f"orphan_artifact:{name}")

    def _check_count(key: str, expected: int) -> None:
        value = index.get(key)
        if _native_int(value) and value != expected:
            errors.append(
                f"{key}_mismatch:{value}!={expected}"
            )

    _check_count("successful_replays", len(entries))
    _check_count("failed_replays", len(failures))
    _check_count(
        "requested_replays",
        len(entries) + len(failures),
    )
    _check_count("samples", aggregate_samples)
    _check_count("compressed_bytes", aggregate_compressed)
    _check_count("selected_players_seen", aggregate_seen)
    _check_count(
        "unknown_input_samples_skipped",
        aggregate_skipped,
    )

    try:
        index_bytes_after = _read_regular_bytes(index_path)
    except AuditInputError as exc:
        errors.append(f"index_recheck:{exc}")
        index_bytes_after = b""
    if index_bytes_after != index_bytes_before:
        errors.append("index_changed_during_audit")

    inventory_sha256 = hashlib.sha256(
        "".join(inventory_lines).encode("utf-8")
    ).hexdigest()
    errors = sorted(set(errors))
    return {
        "schema": AUDIT_SCHEMA,
        "clean": not errors,
        "index_schema": index.get("schema"),
        "manifest_schema": index.get("manifest_schema"),
        "analysis_version": index.get("analysis_version"),
        "split": index.get("split"),
        "sample_every_ticks": index.get("sample_every_ticks"),
        "requested_replays": index.get("requested_replays"),
        "successful_replays": index.get("successful_replays"),
        "failed_replays": index.get("failed_replays"),
        "audited_shards": len(inventory_lines),
        "samples": aggregate_samples,
        "compressed_bytes": aggregate_compressed,
        "uncompressed_bytes": aggregate_uncompressed,
        "selected_players_seen": aggregate_seen,
        "unknown_input_samples_skipped": aggregate_skipped,
        "index_sha256": index_sha256,
        "inventory_sha256": inventory_sha256,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="haxlab-shard-audit"
    )
    parser.add_argument(
        "--shard-dir",
        type=Path,
        required=True,
        help=(
            "Finalized train/holdout shard directory "
            "containing _index.json"
        ),
    )
    args = parser.parse_args()
    try:
        receipt = audit_shard_directory(args.shard_dir)
    except AuditInputError as exc:
        receipt = {
            "schema": AUDIT_SCHEMA,
            "clean": False,
            "errors": [str(exc)],
        }
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
