from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.live.inference import resolve_version_dir


def _version(root: Path, name: str) -> Path:
    version = root / "versions" / name
    version.mkdir(parents=True)
    (version / "metrics.json").write_text("{}\n", encoding="utf-8")
    (version / "model.npz").write_bytes(b"model-placeholder")
    return version


def _live_pointer(
    root: Path,
    version: str,
    *,
    validation_stage: str = "live",
    source_validation_stage: str = "canary",
    evidence_validated: bool = True,
    evidence_version: str | None = None,
) -> Path:
    version_dir = root / "versions" / version
    evidence_dir = (
        root / "validations" / version / source_validation_stage
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence = {
        "validated": evidence_validated,
        "candidate": {
            "version_id": version if evidence_version is None else evidence_version
        },
    }
    evidence_bytes = (
        json.dumps(evidence, sort_keys=True) + "\n"
    ).encode("utf-8")
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    evidence_path = evidence_dir / f"{evidence_sha}.json"
    evidence_path.write_bytes(evidence_bytes)

    payload = {
        "schema": "haxlab-champion-pointer-v1",
        "version_id": version,
        "model_path": str(version_dir / "model.npz"),
        "metrics_path": str(version_dir / "metrics.json"),
        "validation_stage": validation_stage,
        "source_validation_stage": source_validation_stage,
        "validation_evidence_path": str(evidence_path),
        "validation_evidence_sha256": evidence_sha,
    }
    (root / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )
    return evidence_path


def test_resolve_version_uses_activated_live_pointer(tmp_path: Path) -> None:
    promoted = _version(tmp_path, "champion-v1")
    _version(tmp_path, "newer-current-v2")
    _live_pointer(tmp_path, "champion-v1")
    (tmp_path / "current.json").write_text(
        json.dumps(
            {
                "schema": "haxlab-champion-pointer-v1",
                "version_id": "newer-current-v2",
                "validation_stage": "canary",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    resolved = resolve_version_dir(tmp_path)

    assert resolved == promoted


def test_current_pointer_alone_cannot_authorize_live_inference(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "canary-v1")
    (tmp_path / "current.json").write_text(
        json.dumps(
            {
                "schema": "haxlab-champion-pointer-v1",
                "version_id": "canary-v1",
                "validation_stage": "canary",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        FileNotFoundError,
        match="refusing to select current/canary or unpromoted versions",
    ):
        resolve_version_dir(tmp_path)


def test_resolve_version_fails_closed_without_live_pointer(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "candidate-v1")
    _version(tmp_path, "candidate-v2")

    with pytest.raises(
        FileNotFoundError,
        match="refusing to select current/canary or unpromoted versions",
    ):
        resolve_version_dir(tmp_path)


def test_resolve_version_fails_closed_on_corrupt_live_pointer(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "candidate-v1")
    (tmp_path / "live.json").write_text(
        '{"version_id": ',
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


@pytest.mark.parametrize(
    ("validation_stage", "source_stage"),
    [
        ("canary", "canary"),
        ("promotion", "canary"),
        ("live", "promotion"),
        ("live", "multi_replay"),
    ],
)
def test_live_pointer_requires_live_stage_and_canary_source(
    tmp_path: Path,
    validation_stage: str,
    source_stage: str,
) -> None:
    _version(tmp_path, "candidate-v1")
    _live_pointer(
        tmp_path,
        "candidate-v1",
        validation_stage=validation_stage,
        source_validation_stage=source_stage,
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_explicit_version_remains_available_for_scoped_probe(
    tmp_path: Path,
) -> None:
    candidate = _version(tmp_path, "candidate-v1")

    resolved = resolve_version_dir(tmp_path, "candidate-v1")

    assert resolved == candidate


def test_explicit_version_rejects_parent_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.json").write_text("{}\n", encoding="utf-8")
    (outside / "model.npz").write_bytes(b"model-placeholder")

    with pytest.raises(FileNotFoundError, match="not found or unsafe"):
        resolve_version_dir(tmp_path, "../outside")


def test_explicit_version_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.json").write_text("{}\n", encoding="utf-8")
    (outside / "model.npz").write_bytes(b"model-placeholder")
    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "candidate-link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FileNotFoundError, match="not found or unsafe"):
        resolve_version_dir(tmp_path, "candidate-link")


def test_live_pointer_rejects_unsafe_version_reference(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.json").write_text("{}\n", encoding="utf-8")
    (outside / "model.npz").write_bytes(b"model-placeholder")
    payload = {
        "schema": "haxlab-champion-pointer-v1",
        "version_id": "../outside",
        "model_path": str(outside / "model.npz"),
        "metrics_path": str(outside / "metrics.json"),
        "validation_stage": "live",
        "source_validation_stage": "canary",
    }
    (tmp_path / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_requires_complete_loadable_version(
    tmp_path: Path,
) -> None:
    version = tmp_path / "versions" / "incomplete-v1"
    version.mkdir(parents=True)
    (version / "metrics.json").write_text("{}\n", encoding="utf-8")
    _live_pointer(tmp_path, "incomplete-v1")

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_paths_must_match_version_artifacts(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _version(tmp_path, "other-v2")
    _live_pointer(tmp_path, "champion-v1")
    payload = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    payload["model_path"] = str(tmp_path / "versions" / "other-v2" / "model.npz")
    (tmp_path / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_explicit_version_rejects_redirected_versions_root(
    tmp_path: Path,
) -> None:
    external_registry = tmp_path / "external-registry"
    external_versions = external_registry / "versions"
    external_version = external_versions / "candidate-v1"
    external_version.mkdir(parents=True)
    (external_version / "metrics.json").write_text("{}\n", encoding="utf-8")
    (external_version / "model.npz").write_bytes(b"model-placeholder")

    root = tmp_path / "live-root"
    root.mkdir()
    (root / "versions").symlink_to(external_versions, target_is_directory=True)

    with pytest.raises(FileNotFoundError, match="not found or unsafe"):
        resolve_version_dir(root, "candidate-v1")


def test_live_pointer_rejects_tampered_validation_evidence(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    evidence_path = _live_pointer(tmp_path, "champion-v1")
    evidence_path.write_text(
        json.dumps(
            {
                "validated": True,
                "candidate": {"version_id": "champion-v1"},
                "tampered": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_failed_validation_evidence(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(
        tmp_path,
        "champion-v1",
        evidence_validated=False,
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_validation_version_mismatch(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(
        tmp_path,
        "champion-v1",
        evidence_version="different-v2",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_validation_evidence_outside_registry(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(tmp_path, "champion-v1")
    payload = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))

    outside = tmp_path / "outside-evidence.json"
    outside_bytes = (
        json.dumps(
            {
                "validated": True,
                "candidate": {"version_id": "champion-v1"},
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    outside.write_bytes(outside_bytes)
    payload["validation_evidence_path"] = str(outside)
    payload["validation_evidence_sha256"] = hashlib.sha256(
        outside_bytes
    ).hexdigest()
    (tmp_path / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_explicit_version_rejects_sibling_version_alias(
    tmp_path: Path,
) -> None:
    target = _version(tmp_path, "champion-v1")
    (tmp_path / "versions" / "alias-v2").symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(FileNotFoundError, match="not found or unsafe"):
        resolve_version_dir(tmp_path, "alias-v2")


@pytest.mark.parametrize("filename", ["model.npz", "metrics.json"])
def test_explicit_version_rejects_symlinked_version_artifact(
    tmp_path: Path,
    filename: str,
) -> None:
    version = _version(tmp_path, "candidate-v1")
    artifact = version / filename
    artifact.unlink()
    outside = tmp_path / f"outside-{filename}"
    if filename.endswith(".json"):
        outside.write_text("{}\n", encoding="utf-8")
    else:
        outside.write_bytes(b"model-placeholder")
    artifact.symlink_to(outside)

    with pytest.raises(FileNotFoundError, match="not found or unsafe"):
        resolve_version_dir(tmp_path, "candidate-v1")


def test_live_pointer_rejects_redirected_validations_root(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    external = tmp_path / "external-validations"
    stage = external / "champion-v1" / "canary"
    stage.mkdir(parents=True)
    evidence_bytes = (
        json.dumps(
            {
                "validated": True,
                "candidate": {"version_id": "champion-v1"},
            },
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    evidence_sha = hashlib.sha256(evidence_bytes).hexdigest()
    evidence_path = stage / f"{evidence_sha}.json"
    evidence_path.write_bytes(evidence_bytes)
    (tmp_path / "validations").symlink_to(external, target_is_directory=True)
    version_dir = tmp_path / "versions" / "champion-v1"
    payload = {
        "schema": "haxlab-champion-pointer-v1",
        "version_id": "champion-v1",
        "model_path": str(version_dir / "model.npz"),
        "metrics_path": str(version_dir / "metrics.json"),
        "validation_stage": "live",
        "source_validation_stage": "canary",
        "validation_evidence_path": str(evidence_path),
        "validation_evidence_sha256": evidence_sha,
    }
    (tmp_path / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_relative_artifact_paths(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(tmp_path, "champion-v1")
    payload = json.loads((tmp_path / "live.json").read_text(encoding="utf-8"))
    payload["model_path"] = "versions/champion-v1/model.npz"
    (tmp_path / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)

def test_live_pointer_rejects_symlinked_pointer_file(tmp_path: Path) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(tmp_path, "champion-v1")
    live_path = tmp_path / "live.json"
    redirected = tmp_path / "redirected-live.json"
    live_path.rename(redirected)
    live_path.symlink_to(redirected)

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_symlinked_validation_version_dir(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(tmp_path, "champion-v1")
    version_dir = tmp_path / "validations" / "champion-v1"
    redirected = tmp_path / "validations" / "redirected-version"
    version_dir.rename(redirected)
    version_dir.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)


def test_live_pointer_rejects_symlinked_validation_stage_dir(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "champion-v1")
    _live_pointer(tmp_path, "champion-v1")
    stage_dir = tmp_path / "validations" / "champion-v1" / "canary"
    redirected = stage_dir.parent / "redirected-canary"
    stage_dir.rename(redirected)
    stage_dir.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)

