from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from typing import Any

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS, RECEIPT_SCHEMA


GENERATION_COMMIT_SCHEMA = "haxlab-m0-generation-commit-v1"
GENERATION_POINTER_SCHEMA = "haxlab-m0-generation-pointer-v1"
MAX_GENERATION_COMMIT_BYTES = 16 * 1024
MAX_GENERATION_POINTER_BYTES = 1024
GENERATIONS_DIRECTORY = "generations"
GENERATION_COMMIT_FILE = "generation-commit.json"
CURRENT_GENERATION_POINTER_FILE = "current-generation.json"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class GenerationCommitError(ValueError):
    """Raised when generation commit evidence is malformed or inconsistent."""


def _canonical_bytes(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        dict(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _parse_json_object(payload: bytes, *, label: str, max_bytes: int) -> dict[str, Any]:
    if type(payload) is not bytes:
        raise GenerationCommitError(f"{label} bytes must be native bytes")
    if not payload or len(payload) > max_bytes:
        raise GenerationCommitError(f"{label} byte size is invalid")

    def _object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise GenerationCommitError(f"{label} has duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_from_pairs,
        )
    except GenerationCommitError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise GenerationCommitError(f"{label} is not valid UTF-8 JSON") from exc
    if type(value) is not dict:
        raise GenerationCommitError(f"{label} must be a JSON object")
    return value


def _require_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise GenerationCommitError(f"{field} must be a lowercase SHA-256")
    return value


def _validated_receipt(
    receipt: Mapping[str, Any],
) -> tuple[str, tuple[dict[str, object], ...]]:
    if not isinstance(receipt, Mapping):
        raise GenerationCommitError("dataset receipt must be an object")

    required = {"schema", "artifact_count", "artifacts", "receipt_sha256"}
    if set(receipt) != required:
        raise GenerationCommitError("dataset receipt has unexpected fields")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise GenerationCommitError("dataset receipt schema mismatch")

    artifact_count = receipt.get("artifact_count")
    if type(artifact_count) is not int or artifact_count != len(M0_ARTIFACTS):
        raise GenerationCommitError("dataset receipt artifact_count mismatch")

    raw_artifacts = receipt.get("artifacts")
    if type(raw_artifacts) is not list or len(raw_artifacts) != len(M0_ARTIFACTS):
        raise GenerationCommitError("dataset receipt artifacts mismatch")

    artifacts: list[dict[str, object]] = []
    for index, expected_name in enumerate(M0_ARTIFACTS):
        raw = raw_artifacts[index]
        if type(raw) is not dict:
            raise GenerationCommitError(f"artifact {index} must be an object")
        if set(raw) != {"name", "size_bytes", "sha256"}:
            raise GenerationCommitError(f"artifact {index} has unexpected fields")
        if raw.get("name") != expected_name:
            raise GenerationCommitError(
                f"artifact {index} must be canonical name {expected_name}"
            )
        size_bytes = raw.get("size_bytes")
        if type(size_bytes) is not int or size_bytes < 0:
            raise GenerationCommitError(f"artifact {index} size_bytes is invalid")
        sha256 = _require_sha256(raw.get("sha256"), field=f"artifact {index} sha256")
        artifacts.append(
            {"name": expected_name, "size_bytes": size_bytes, "sha256": sha256}
        )

    receipt_sha256 = _require_sha256(
        receipt.get("receipt_sha256"), field="receipt_sha256"
    )
    receipt_core: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
    }
    expected_receipt_sha256 = hashlib.sha256(
        _canonical_bytes(receipt_core)
    ).hexdigest()
    if receipt_sha256 != expected_receipt_sha256:
        raise GenerationCommitError("dataset receipt digest mismatch")

    return receipt_sha256, tuple(artifacts)


def build_generation_commit(receipt: Mapping[str, Any]) -> dict[str, object]:
    """Build immutable commit metadata for one complete five-artifact M0 generation."""

    receipt_sha256, artifacts = _validated_receipt(receipt)
    generation_id = f"m0-{receipt_sha256}"
    core: dict[str, object] = {
        "schema": GENERATION_COMMIT_SCHEMA,
        "generation_id": generation_id,
        "receipt_schema": RECEIPT_SCHEMA,
        "receipt_sha256": receipt_sha256,
        "artifact_count": len(artifacts),
        "artifacts": [dict(row) for row in artifacts],
    }
    commit_sha256 = hashlib.sha256(_canonical_bytes(core)).hexdigest()
    return {**core, "commit_sha256": commit_sha256}


def generation_commit_bytes(receipt: Mapping[str, Any]) -> bytes:
    """Return canonical persisted bytes for a generation commit marker."""

    return _canonical_bytes(build_generation_commit(receipt)) + b"\n"


def _validated_commit(commit: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(commit, Mapping):
        raise GenerationCommitError("generation commit must be an object")

    required = {
        "schema",
        "generation_id",
        "receipt_schema",
        "receipt_sha256",
        "artifact_count",
        "artifacts",
        "commit_sha256",
    }
    if set(commit) != required:
        raise GenerationCommitError("generation commit has unexpected fields")
    if commit.get("schema") != GENERATION_COMMIT_SCHEMA:
        raise GenerationCommitError("generation commit schema mismatch")
    if commit.get("receipt_schema") != RECEIPT_SCHEMA:
        raise GenerationCommitError("generation commit receipt schema mismatch")

    receipt_sha256 = _require_sha256(
        commit.get("receipt_sha256"), field="receipt_sha256"
    )
    if commit.get("generation_id") != f"m0-{receipt_sha256}":
        raise GenerationCommitError("generation_id does not match receipt")

    synthetic_receipt: dict[str, object] = {
        "schema": RECEIPT_SCHEMA,
        "artifact_count": commit.get("artifact_count"),
        "artifacts": commit.get("artifacts"),
        "receipt_sha256": receipt_sha256,
    }
    _validated_receipt(synthetic_receipt)

    commit_sha256 = _require_sha256(
        commit.get("commit_sha256"), field="commit_sha256"
    )
    core = {key: commit[key] for key in required - {"commit_sha256"}}
    expected_commit_sha256 = hashlib.sha256(_canonical_bytes(core)).hexdigest()
    if commit_sha256 != expected_commit_sha256:
        raise GenerationCommitError("generation commit digest mismatch")

    return {key: commit[key] for key in required}


def _validated_pointer(pointer: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(pointer, Mapping):
        raise GenerationCommitError("generation pointer must be an object")
    required = {"schema", "generation_id", "receipt_sha256", "commit_sha256"}
    if set(pointer) != required:
        raise GenerationCommitError("generation pointer has unexpected fields")
    if pointer.get("schema") != GENERATION_POINTER_SCHEMA:
        raise GenerationCommitError("generation pointer schema mismatch")

    receipt_sha256 = _require_sha256(
        pointer.get("receipt_sha256"), field="receipt_sha256"
    )
    commit_sha256 = _require_sha256(
        pointer.get("commit_sha256"), field="commit_sha256"
    )
    generation_id = pointer.get("generation_id")
    if generation_id != f"m0-{receipt_sha256}":
        raise GenerationCommitError("generation pointer id does not match receipt")

    return {
        "schema": GENERATION_POINTER_SCHEMA,
        "generation_id": generation_id,
        "receipt_sha256": receipt_sha256,
        "commit_sha256": commit_sha256,
    }


def build_generation_pointer(commit: Mapping[str, Any]) -> dict[str, object]:
    """Build the small pointer payload intended for one final atomic swap."""

    valid = _validated_commit(commit)
    return {
        "schema": GENERATION_POINTER_SCHEMA,
        "generation_id": valid["generation_id"],
        "receipt_sha256": valid["receipt_sha256"],
        "commit_sha256": valid["commit_sha256"],
    }


def generation_pointer_bytes(commit: Mapping[str, Any]) -> bytes:
    """Return canonical bytes for the atomically replaceable generation pointer."""

    return _canonical_bytes(build_generation_pointer(commit)) + b"\n"


def generation_directory(commit: Mapping[str, Any]) -> str:
    """Return the canonical relative directory for one committed generation."""

    valid = _validated_commit(commit)
    return f"{GENERATIONS_DIRECTORY}/{valid['generation_id']}"


def generation_commit_path(commit: Mapping[str, Any]) -> str:
    """Return the canonical relative generation-commit path."""

    return f"{generation_directory(commit)}/{GENERATION_COMMIT_FILE}"


def parse_generation_commit_bytes(payload: bytes) -> dict[str, object]:
    """Parse and validate canonical persisted generation-commit bytes."""

    raw = _parse_json_object(
        payload,
        label="generation commit",
        max_bytes=MAX_GENERATION_COMMIT_BYTES,
    )
    valid = _validated_commit(raw)
    if payload != _canonical_bytes(valid) + b"\n":
        raise GenerationCommitError("generation commit bytes are not canonical")
    return valid


def parse_generation_pointer_bytes(payload: bytes) -> dict[str, object]:
    """Parse and validate canonical persisted generation-pointer bytes."""

    raw = _parse_json_object(
        payload,
        label="generation pointer",
        max_bytes=MAX_GENERATION_POINTER_BYTES,
    )
    valid = _validated_pointer(raw)
    if payload != _canonical_bytes(valid) + b"\n":
        raise GenerationCommitError("generation pointer bytes are not canonical")
    return valid


def validate_generation_pointer(
    pointer: Mapping[str, Any],
    commit: Mapping[str, Any],
) -> dict[str, object]:
    """Bind a reader-visible pointer to the exact validated generation commit."""

    valid_pointer = _validated_pointer(pointer)
    expected = build_generation_pointer(commit)
    if valid_pointer != expected:
        raise GenerationCommitError("generation pointer does not match commit")
    return valid_pointer
