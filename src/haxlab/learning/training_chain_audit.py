from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from haxlab.learning import manifest_audit, shard_audit, shard_bundle_audit

AUDIT_SCHEMA = "haxlab-training-chain-audit-v1"
_COPIED_FIELDS = ("raw_path", "selected_player_ids", "selected_players", "example_weight")


def _error_receipt(errors: list[str]) -> dict[str, Any]:
    return {"schema": AUDIT_SCHEMA, "clean": False, "errors": sorted(set(errors))}


def _manifest_rows(payload: dict[str, Any], *, split: str) -> dict[str, dict[str, Any]]:
    rows = payload.get(f"{split}_replays")
    if type(rows) is not list:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if type(row) is not dict:
            continue
        replay_sha = row.get("replay_sha256")
        if shard_audit._canonical_sha(replay_sha):
            result[replay_sha] = row
    return result


def _index_inventory(index: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    successes: dict[str, dict[str, Any]] = {}
    failures: set[str] = set()
    for row in index.get("entries") if type(index.get("entries")) is list else []:
        if type(row) is dict and shard_audit._canonical_sha(row.get("replay_sha256")):
            successes[row["replay_sha256"]] = row
    for row in index.get("failures") if type(index.get("failures")) is list else []:
        if type(row) is dict and shard_audit._canonical_sha(row.get("replay_sha256")):
            failures.add(row["replay_sha256"])
    return successes, failures


def _compare_split(
    *,
    split: str,
    manifest_rows: dict[str, dict[str, Any]],
    index: dict[str, Any],
    errors: list[str],
    inventory_lines: list[str],
) -> None:
    successes, failures = _index_inventory(index)
    requested = set(successes) | failures
    expected = set(manifest_rows)

    for replay_sha in sorted(expected - requested):
        errors.append(f"{split}:missing_requested_replay:{replay_sha}")
    for replay_sha in sorted(requested - expected):
        errors.append(f"{split}:unexpected_requested_replay:{replay_sha}")

    for replay_sha in sorted(successes):
        manifest_row = manifest_rows.get(replay_sha)
        if manifest_row is None:
            continue
        shard_row = successes[replay_sha]
        for field in _COPIED_FIELDS:
            if shard_row.get(field) != manifest_row.get(field):
                errors.append(f"{split}:{replay_sha}:manifest_shard_field_mismatch:{field}")

    for replay_sha in sorted(expected):
        status = (
            "success" if replay_sha in successes
            else "failure" if replay_sha in failures
            else "missing"
        )
        row = manifest_rows[replay_sha]
        inventory_lines.append(
            json.dumps(
                {
                    "split": split,
                    "replay_sha256": replay_sha,
                    "status": status,
                    "evidence": {field: row.get(field) for field in _COPIED_FIELDS},
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ) + "\n"
        )


def audit_training_chain(
    *,
    manifest_path: Path,
    train_dir: Path,
    holdout_dir: Path,
) -> dict[str, Any]:
    errors: list[str] = []

    try:
        manifest_before = manifest_audit._secure_read(manifest_path)
        manifest_payload = manifest_audit._load_manifest(manifest_before)
        manifest_receipt = manifest_audit.audit_training_manifest(manifest_path)
    except manifest_audit.ManifestAuditError as exc:
        return _error_receipt([f"manifest:{exc}"])

    expected_manifest_sha = hashlib.sha256(manifest_before).hexdigest()
    if manifest_receipt.get("sha256") != expected_manifest_sha:
        errors.append("manifest_changed_before_manifest_audit")

    try:
        train_index, train_before = shard_bundle_audit._load_index_snapshot(train_dir)
        holdout_index, holdout_before = shard_bundle_audit._load_index_snapshot(holdout_dir)
    except shard_bundle_audit.ShardBundleAuditError as exc:
        return _error_receipt([*errors, f"shard_bundle:{exc}"])

    bundle_receipt = shard_bundle_audit.audit_shard_bundle(
        train_dir=train_dir,
        holdout_dir=holdout_dir,
    )
    if not bundle_receipt.get("clean"):
        errors.extend(f"shard_bundle:{error}" for error in bundle_receipt.get("errors") or [])

    for split, index, index_before in (
        ("train", train_index, train_before),
        ("holdout", holdout_index, holdout_before),
    ):
        if index.get("manifest_path") != str(manifest_path):
            errors.append(
                f"{split}:manifest_path_mismatch:{index.get('manifest_path')!r}!={str(manifest_path)!r}"
            )
        if index.get("manifest_schema") != manifest_payload.get("schema"):
            errors.append(f"{split}:manifest_schema_mismatch")
        if index.get("analysis_version") != manifest_payload.get("analysis_version"):
            errors.append(f"{split}:analysis_version_mismatch")
        receipt_section = bundle_receipt.get(split)
        if type(receipt_section) is dict:
            expected_index_sha = hashlib.sha256(index_before).hexdigest()
            if receipt_section.get("index_sha256") != expected_index_sha:
                errors.append(f"{split}:index_changed_before_bundle_audit")

    manifest_train = _manifest_rows(manifest_payload, split="train")
    manifest_holdout = _manifest_rows(manifest_payload, split="holdout")
    inventory_lines: list[str] = []
    _compare_split(
        split="train",
        manifest_rows=manifest_train,
        index=train_index,
        errors=errors,
        inventory_lines=inventory_lines,
    )
    _compare_split(
        split="holdout",
        manifest_rows=manifest_holdout,
        index=holdout_index,
        errors=errors,
        inventory_lines=inventory_lines,
    )

    try:
        manifest_after = manifest_audit._secure_read(manifest_path)
        train_after = shard_audit._read_regular_bytes(train_dir / "_index.json")
        holdout_after = shard_audit._read_regular_bytes(holdout_dir / "_index.json")
    except (manifest_audit.ManifestAuditError, shard_audit.AuditInputError) as exc:
        errors.append(f"chain_recheck:{exc}")
        manifest_after = b""
        train_after = b""
        holdout_after = b""

    if manifest_after != manifest_before:
        errors.append("manifest_changed_during_chain_audit")
    if train_after != train_before:
        errors.append("train:index_changed_during_chain_audit")
    if holdout_after != holdout_before:
        errors.append("holdout:index_changed_during_chain_audit")

    inventory_sha256 = hashlib.sha256(
        "".join(sorted(inventory_lines)).encode("utf-8")
    ).hexdigest()
    chain_evidence = {
        "manifest_sha256": expected_manifest_sha,
        "manifest_inventory_sha256": manifest_receipt.get("inventory_sha256"),
        "shard_bundle_inventory_sha256": bundle_receipt.get("inventory_sha256"),
        "inventory_sha256": inventory_sha256,
    }
    chain_sha256 = hashlib.sha256(
        json.dumps(
            chain_evidence,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    errors = sorted(set(errors))
    return {
        "schema": AUDIT_SCHEMA,
        "clean": not errors,
        "analysis_version": manifest_payload.get("analysis_version"),
        "manifest_schema": manifest_payload.get("schema"),
        "manifest_sha256": expected_manifest_sha,
        "manifest_inventory_sha256": manifest_receipt.get("inventory_sha256"),
        "shard_bundle_inventory_sha256": bundle_receipt.get("inventory_sha256"),
        "train_replays": len(manifest_train),
        "holdout_replays": len(manifest_holdout),
        "inventory_sha256": inventory_sha256,
        "chain_sha256": chain_sha256,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-training-chain-audit")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--train-dir", type=Path, required=True)
    parser.add_argument("--holdout-dir", type=Path, required=True)
    args = parser.parse_args()
    receipt = audit_training_chain(
        manifest_path=args.manifest,
        train_dir=args.train_dir,
        holdout_dir=args.holdout_dir,
    )
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 0 if receipt["clean"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
