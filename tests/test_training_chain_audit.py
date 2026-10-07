from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.learning import manifest_audit, shard_audit, shard_bundle_audit
from haxlab.learning.training_chain_audit import AUDIT_SCHEMA, audit_training_chain


TRAIN_SHA = "a" * 64
HOLDOUT_SHA = "b" * 64
MANIFEST_PATH = Path("/derived/training/manifest.json")


def _manifest() -> dict:
    common = {
        "raw_path": "/raw/replay.hbr2",
        "selected_player_ids": ["auth:alpha"],
        "selected_players": [
            {"replay_player_id": 7, "identity": "auth:alpha", "samples": 12}
        ],
        "example_weight": 1.0,
    }
    return {
        "schema": "haxlab-human-imitation-manifest-v3",
        "analysis_version": "state-pass-v4",
        "train_replays": [{"replay_sha256": TRAIN_SHA, **common}],
        "holdout_replays": [
            {
                "replay_sha256": HOLDOUT_SHA,
                **{**common, "raw_path": "/raw/holdout.hbr2"},
            }
        ],
    }


def _indexes() -> tuple[dict, dict]:
    manifest = _manifest()
    train = {
        "manifest_path": str(MANIFEST_PATH),
        "manifest_schema": manifest["schema"],
        "analysis_version": manifest["analysis_version"],
        "entries": [dict(manifest["train_replays"][0])],
        "failures": [],
    }
    holdout = {
        "manifest_path": str(MANIFEST_PATH),
        "manifest_schema": manifest["schema"],
        "analysis_version": manifest["analysis_version"],
        "entries": [],
        "failures": [{"replay_sha256": HOLDOUT_SHA, "error": "decoder_failed"}],
    }
    return train, holdout


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _install_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    manifest: dict | None = None,
    train: dict | None = None,
    holdout: dict | None = None,
    bundle_clean: bool = True,
    bundle_errors: list[str] | None = None,
    mutate_manifest_on_recheck: bool = False,
) -> None:
    manifest = manifest or _manifest()
    default_train, default_holdout = _indexes()
    train = train or default_train
    holdout = holdout or default_holdout
    manifest_bytes = _json_bytes(manifest)
    train_bytes = _json_bytes(train)
    holdout_bytes = _json_bytes(holdout)

    reads = 0

    def secure_read(path: Path) -> bytes:
        nonlocal reads
        assert path == MANIFEST_PATH
        reads += 1
        if mutate_manifest_on_recheck and reads > 1:
            return manifest_bytes + b" "
        return manifest_bytes

    monkeypatch.setattr(manifest_audit, "_secure_read", secure_read)
    monkeypatch.setattr(manifest_audit, "_load_manifest", lambda payload: json.loads(payload))
    monkeypatch.setattr(
        manifest_audit,
        "audit_training_manifest",
        lambda path: {
            "schema": manifest_audit.AUDIT_SCHEMA,
            "ok": True,
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "inventory_sha256": "c" * 64,
        },
    )

    def load_index_snapshot(path: Path) -> tuple[dict, bytes]:
        if path.name == "train":
            return train, train_bytes
        if path.name == "holdout":
            return holdout, holdout_bytes
        raise AssertionError(path)

    monkeypatch.setattr(shard_bundle_audit, "_load_index_snapshot", load_index_snapshot)
    monkeypatch.setattr(
        shard_bundle_audit,
        "audit_shard_bundle",
        lambda **kwargs: {
            "schema": shard_bundle_audit.AUDIT_SCHEMA,
            "clean": bundle_clean,
            "inventory_sha256": "d" * 64,
            "train": {"index_sha256": hashlib.sha256(train_bytes).hexdigest()},
            "holdout": {"index_sha256": hashlib.sha256(holdout_bytes).hexdigest()},
            "errors": bundle_errors or [],
        },
    )

    def read_index(path: Path) -> bytes:
        return train_bytes if path.parent.name == "train" else holdout_bytes

    monkeypatch.setattr(shard_audit, "_read_regular_bytes", read_index)


def _audit() -> dict:
    return audit_training_chain(
        manifest_path=MANIFEST_PATH,
        train_dir=Path("/shards/train"),
        holdout_dir=Path("/shards/holdout"),
    )


def test_chain_audit_accepts_exact_success_and_failed_request_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dependencies(monkeypatch)

    first = _audit()
    second = _audit()

    assert first == second
    assert first["schema"] == AUDIT_SCHEMA
    assert first["clean"] is True
    assert first["train_replays"] == 1
    assert first["holdout_replays"] == 1
    assert first["manifest_inventory_sha256"] == "c" * 64
    assert first["shard_bundle_inventory_sha256"] == "d" * 64
    assert len(first["inventory_sha256"]) == 64
    assert len(first["chain_sha256"]) == 64
    assert first["errors"] == []


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (
            lambda train, holdout: holdout.update({"failures": []}),
            f"holdout:missing_requested_replay:{HOLDOUT_SHA}",
        ),
        (
            lambda train, holdout: train["failures"].append(
                {"replay_sha256": HOLDOUT_SHA, "error": "wrong_split"}
            ),
            f"train:unexpected_requested_replay:{HOLDOUT_SHA}",
        ),
    ],
)
def test_chain_audit_rejects_inventory_omission_or_split_reassignment(
    monkeypatch: pytest.MonkeyPatch,
    mutator,
    expected: str,
) -> None:
    train, holdout = _indexes()
    mutator(train, holdout)
    _install_dependencies(monkeypatch, train=train, holdout=holdout)

    receipt = _audit()

    assert receipt["clean"] is False
    assert expected in receipt["errors"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("raw_path", "/raw/drift.hbr2"),
        ("selected_player_ids", ["auth:other"]),
        (
            "selected_players",
            [{"replay_player_id": 8, "identity": "auth:alpha", "samples": 12}],
        ),
        ("example_weight", 1.5),
    ],
)
def test_chain_audit_rejects_copied_training_evidence_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    train, holdout = _indexes()
    train["entries"][0][field] = value
    _install_dependencies(monkeypatch, train=train, holdout=holdout)

    receipt = _audit()

    assert receipt["clean"] is False
    assert (
        f"train:{TRAIN_SHA}:manifest_shard_field_mismatch:{field}"
        in receipt["errors"]
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("manifest_path", "/other/manifest.json", "train:manifest_path_mismatch:"),
        ("analysis_version", "state-pass-v5", "train:analysis_version_mismatch"),
        ("manifest_schema", "wrong-schema", "train:manifest_schema_mismatch"),
    ],
)
def test_chain_audit_rejects_index_provenance_drift(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected: str,
) -> None:
    train, holdout = _indexes()
    train[field] = value
    _install_dependencies(monkeypatch, train=train, holdout=holdout)

    receipt = _audit()

    assert receipt["clean"] is False
    assert any(error.startswith(expected) for error in receipt["errors"])


def test_chain_audit_propagates_manifest_audit_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dependencies(monkeypatch)

    def fail_manifest(path: Path) -> dict:
        raise manifest_audit.ManifestAuditError("invalid_manifest")

    monkeypatch.setattr(
        manifest_audit,
        "audit_training_manifest",
        fail_manifest,
    )

    receipt = _audit()

    assert receipt == {
        "schema": AUDIT_SCHEMA,
        "clean": False,
        "errors": ["manifest:invalid_manifest"],
    }


def test_chain_audit_propagates_underlying_bundle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dependencies(
        monkeypatch,
        bundle_clean=False,
        bundle_errors=["train:directory:corrupt_shard"],
    )

    receipt = _audit()

    assert receipt["clean"] is False
    assert "shard_bundle:train:directory:corrupt_shard" in receipt["errors"]


def test_chain_audit_detects_manifest_mutation_during_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_dependencies(monkeypatch, mutate_manifest_on_recheck=True)

    receipt = _audit()

    assert receipt["clean"] is False
    assert "manifest_changed_during_chain_audit" in receipt["errors"]
