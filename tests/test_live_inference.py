from __future__ import annotations

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
) -> None:
    version_dir = root / "versions" / version
    payload = {
        "schema": "haxlab-champion-pointer-v1",
        "version_id": version,
        "model_path": str(version_dir / "model.npz"),
        "metrics_path": str(version_dir / "metrics.json"),
        "validation_stage": validation_stage,
        "source_validation_stage": source_validation_stage,
    }
    (root / "live.json").write_text(
        json.dumps(payload) + "\n",
        encoding="utf-8",
    )


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
