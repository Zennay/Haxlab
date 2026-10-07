from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from haxlab.learning import shard_audit


AUDIT_SCHEMA = "haxlab-imitation-shard-bundle-audit-v1"


class ShardBundleAuditError(ValueError):
    """Raised when a shard-pair index cannot be read safely."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ShardBundleAuditError(
                f"duplicate_json_key:{key}"
            )
        result[key] = value
    return result


def _load_index_snapshot(
    shard_dir: Path,
) -> tuple[dict[str, Any], bytes]:
    path = shard_dir / "_index.json"
    try:
        payload = shard_audit._read_regular_bytes(path)
    except shard_audit.AuditInputError as exc:
        raise ShardBundleAuditError(
            f"{shard_dir.name}:index:{exc}"
        ) from exc
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_unique_object,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise ShardBundleAuditError(
            f"{shard_dir.name}:invalid_index_json:{exc}"
        ) from exc
    if type(value) is not dict:
        raise ShardBundleAuditError(
            f"{shard_dir.name}:index_not_object"
        )
    return value, payload


def _inventory(
    index: dict[str, Any],
    *,
    label: str,
    errors: list[str],
) -> tuple[set[str], list[str]]:
    ordered: list[str] = []
    seen: set[str] = set()

    for group_name, status in (
        ("entries", "success"),
        ("failures", "failure"),
    ):
        rows = index.get(group_name)
        if type(rows) is not list:
            errors.append(f"{label}:{group_name}:not_list")
            continue
        for position, row in enumerate(rows):
            if type(row) is not dict:
                errors.append(
                    f"{label}:{group_name}[{position}]:not_object"
                )
                continue
            replay_sha = row.get("replay_sha256")
            if not shard_audit._canonical_sha(replay_sha):
                errors.append(
                    f"{label}:{group_name}[{position}]:"
                    "invalid_replay_sha256"
                )
                continue
            if replay_sha in seen:
                errors.append(
                    f"{label}:{replay_sha}:duplicate_identity"
                )
                continue
            seen.add(replay_sha)
            ordered.append(
                f"{label}\t{status}\t{replay_sha}\n"
            )

    requested = index.get("requested_replays")
    if (
        type(requested) is int
        and requested >= 0
        and requested != len(seen)
    ):
        errors.append(
            f"{label}:requested_replays_mismatch:"
            f"{requested}!={len(seen)}"
        )
    return seen, ordered


def _directory_receipt(
    shard_dir: Path,
    *,
    label: str,
    index_bytes: bytes,
    errors: list[str],
) -> dict[str, Any]:
    try:
        receipt = shard_audit.audit_shard_directory(
            shard_dir
        )
    except shard_audit.AuditInputError as exc:
        errors.append(f"{label}:directory:{exc}")
        return {}

    if not receipt.get("clean"):
        for error in receipt.get("errors") or []:
            errors.append(
                f"{label}:directory:{error}"
            )

    expected_sha = hashlib.sha256(
        index_bytes
    ).hexdigest()
    if receipt.get("index_sha256") != expected_sha:
        errors.append(
            f"{label}:index_changed_before_directory_audit"
        )
    return receipt


def audit_shard_bundle(
    *,
    train_dir: Path,
    holdout_dir: Path,
) -> dict[str, Any]:
    errors: list[str] = []

    if train_dir == holdout_dir:
        errors.append(
            "train_holdout_directory_alias"
        )

    try:
        train_index, train_before = _load_index_snapshot(
            train_dir
        )
        holdout_index, holdout_before = _load_index_snapshot(
            holdout_dir
        )
    except ShardBundleAuditError as exc:
        return {
            "schema": AUDIT_SCHEMA,
            "clean": False,
            "errors": [str(exc)],
        }

    train_receipt = _directory_receipt(
        train_dir,
        label="train",
        index_bytes=train_before,
        errors=errors,
    )
    holdout_receipt = _directory_receipt(
        holdout_dir,
        label="holdout",
        index_bytes=holdout_before,
        errors=errors,
    )

    if train_index.get("split") != "train":
        errors.append(
            f"train:split:{train_index.get('split')!r}"
        )
    if holdout_index.get("split") != "holdout":
        errors.append(
            f"holdout:split:{holdout_index.get('split')!r}"
        )

    for field in (
        "schema",
        "manifest_schema",
        "analysis_version",
        "manifest_path",
        "sample_every_ticks",
    ):
        if train_index.get(field) != holdout_index.get(
            field
        ):
            errors.append(
                f"cross_split_{field}_mismatch:"
                f"{train_index.get(field)!r}!="
                f"{holdout_index.get(field)!r}"
            )

    train_ids, train_lines = _inventory(
        train_index,
        label="train",
        errors=errors,
    )
    holdout_ids, holdout_lines = _inventory(
        holdout_index,
        label="holdout",
        errors=errors,
    )

    overlap = sorted(train_ids & holdout_ids)
    for replay_sha in overlap:
        errors.append(
            f"cross_split_replay_leakage:{replay_sha}"
        )

    try:
        train_after = shard_audit._read_regular_bytes(
            train_dir / "_index.json"
        )
        holdout_after = shard_audit._read_regular_bytes(
            holdout_dir / "_index.json"
        )
    except shard_audit.AuditInputError as exc:
        errors.append(f"index_recheck:{exc}")
        train_after = b""
        holdout_after = b""

    if train_after != train_before:
        errors.append(
            "train:index_changed_during_bundle_audit"
        )
    if holdout_after != holdout_before:
        errors.append(
            "holdout:index_changed_during_bundle_audit"
        )

    inventory_sha256 = hashlib.sha256(
        "".join(
            sorted(train_lines + holdout_lines)
        ).encode("utf-8")
    ).hexdigest()

    errors = sorted(set(errors))
    return {
        "schema": AUDIT_SCHEMA,
        "clean": not errors,
        "analysis_version": train_index.get(
            "analysis_version"
        ),
        "manifest_schema": train_index.get(
            "manifest_schema"
        ),
        "manifest_path": train_index.get(
            "manifest_path"
        ),
        "sample_every_ticks": train_index.get(
            "sample_every_ticks"
        ),
        "train": {
            "requested_replays": train_index.get(
                "requested_replays"
            ),
            "successful_replays": train_index.get(
                "successful_replays"
            ),
            "failed_replays": train_index.get(
                "failed_replays"
            ),
            "samples": train_receipt.get("samples"),
            "index_sha256": hashlib.sha256(
                train_before
            ).hexdigest(),
            "shard_inventory_sha256": (
                train_receipt.get("inventory_sha256")
            ),
        },
        "holdout": {
            "requested_replays": holdout_index.get(
                "requested_replays"
            ),
            "successful_replays": holdout_index.get(
                "successful_replays"
            ),
            "failed_replays": holdout_index.get(
                "failed_replays"
            ),
            "samples": holdout_receipt.get("samples"),
            "index_sha256": hashlib.sha256(
                holdout_before
            ).hexdigest(),
            "shard_inventory_sha256": (
                holdout_receipt.get("inventory_sha256")
            ),
        },
        "unique_replays": len(
            train_ids | holdout_ids
        ),
        "inventory_sha256": inventory_sha256,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="haxlab-shard-bundle-audit"
    )
    parser.add_argument(
        "--train-dir",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--holdout-dir",
        type=Path,
        required=True,
    )
    args = parser.parse_args()
    receipt = audit_shard_bundle(
        train_dir=args.train_dir,
        holdout_dir=args.holdout_dir,
    )
    print(
        json.dumps(
            receipt,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if receipt["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
