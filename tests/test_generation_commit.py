from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS, build_dataset_receipt
from haxlab.ingestion.generation_commit import (
    GENERATION_COMMIT_SCHEMA,
    GENERATION_POINTER_SCHEMA,
    GenerationCommitError,
    build_generation_commit,
    build_generation_pointer,
    generation_commit_bytes,
    generation_pointer_bytes,
)


def _write_dataset(root: Path, *, suffix: bytes = b"") -> None:
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": b'{"match_count":1}\n',
        "replays.json": b'[{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]\n',
        "duplicates.json": b'[]\n',
        "reports.json": b'[]\n',
        "matches.jsonl": b'{"match_id":"m-1"}\n' + suffix,
    }
    for name in M0_ARTIFACTS:
        (root / name).write_bytes(payloads[name])


def _receipt(tmp_path: Path, name: str = "dataset") -> dict[str, object]:
    root = tmp_path / name
    _write_dataset(root)
    return build_dataset_receipt(root)


def test_generation_commit_binds_receipt_and_all_five_artifacts(
    tmp_path: Path,
) -> None:
    receipt = _receipt(tmp_path)

    commit = build_generation_commit(receipt)

    assert commit["schema"] == GENERATION_COMMIT_SCHEMA
    assert commit["receipt_sha256"] == receipt["receipt_sha256"]
    assert commit["generation_id"] == f"m0-{receipt['receipt_sha256']}"
    assert commit["artifact_count"] == len(M0_ARTIFACTS)
    assert [row["name"] for row in commit["artifacts"]] == list(M0_ARTIFACTS)
    assert commit["artifacts"] == receipt["artifacts"]
    assert len(commit["commit_sha256"]) == 64


def test_generation_commit_and_pointer_are_relocation_stable(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "elsewhere" / "second"
    _write_dataset(first)
    _write_dataset(second)

    first_receipt = build_dataset_receipt(first)
    second_receipt = build_dataset_receipt(second)
    first_commit = build_generation_commit(first_receipt)
    second_commit = build_generation_commit(second_receipt)

    assert first_receipt == second_receipt
    assert first_commit == second_commit
    assert generation_commit_bytes(first_receipt) == generation_commit_bytes(second_receipt)
    assert generation_pointer_bytes(first_commit) == generation_pointer_bytes(second_commit)


def test_generation_pointer_is_small_and_binds_commit_identity(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    commit = build_generation_commit(receipt)

    pointer = build_generation_pointer(commit)

    assert pointer == {
        "schema": GENERATION_POINTER_SCHEMA,
        "generation_id": commit["generation_id"],
        "receipt_sha256": commit["receipt_sha256"],
        "commit_sha256": commit["commit_sha256"],
    }
    assert generation_pointer_bytes(commit).endswith(b"\n")
    assert json.loads(generation_pointer_bytes(commit)) == pointer


def test_generation_identity_changes_when_artifact_bytes_change(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_dataset(first)
    _write_dataset(second, suffix=b'{"match_id":"m-2"}\n')

    before = build_generation_commit(build_dataset_receipt(first))
    after = build_generation_commit(build_dataset_receipt(second))

    assert before["receipt_sha256"] != after["receipt_sha256"]
    assert before["generation_id"] != after["generation_id"]
    assert before["commit_sha256"] != after["commit_sha256"]


def test_tampered_receipt_artifact_evidence_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    tampered = deepcopy(receipt)
    tampered["artifacts"][0]["size_bytes"] += 1

    with pytest.raises(GenerationCommitError, match="receipt digest mismatch"):
        build_generation_commit(tampered)


def test_tampered_receipt_digest_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    tampered = deepcopy(receipt)
    tampered["receipt_sha256"] = "0" * 64

    with pytest.raises(GenerationCommitError, match="receipt digest mismatch"):
        build_generation_commit(tampered)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_count", True, "artifact_count mismatch"),
        ("artifact_count", 4, "artifact_count mismatch"),
        ("artifacts", {}, "artifacts mismatch"),
        ("schema", "future-schema", "schema mismatch"),
    ],
)
def test_malformed_receipt_contract_fails_closed(
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    receipt = _receipt(tmp_path)
    malformed = deepcopy(receipt)
    malformed[field] = value

    with pytest.raises(GenerationCommitError, match=message):
        build_generation_commit(malformed)


def test_extra_receipt_fields_fail_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    receipt["ok"] = True

    with pytest.raises(GenerationCommitError, match="unexpected fields"):
        build_generation_commit(receipt)


def test_noncanonical_artifact_order_fails_closed(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    malformed = deepcopy(receipt)
    malformed["artifacts"][0], malformed["artifacts"][1] = (
        malformed["artifacts"][1],
        malformed["artifacts"][0],
    )

    with pytest.raises(GenerationCommitError, match="canonical name"):
        build_generation_commit(malformed)


def test_pointer_rejects_tampered_generation_commit(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    commit = build_generation_commit(receipt)
    commit["generation_id"] = "m0-" + "0" * 64

    with pytest.raises(GenerationCommitError, match="generation_id"):
        build_generation_pointer(commit)


def test_pointer_rejects_tampered_commit_digest(tmp_path: Path) -> None:
    receipt = _receipt(tmp_path)
    commit = build_generation_commit(receipt)
    commit["commit_sha256"] = "0" * 64

    with pytest.raises(GenerationCommitError, match="commit digest mismatch"):
        build_generation_pointer(commit)
