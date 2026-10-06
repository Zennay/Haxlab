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


def test_resolve_version_uses_promoted_pointer(tmp_path: Path) -> None:
    promoted = _version(tmp_path, "champion-v1")
    _version(tmp_path, "newer-unpromoted-v2")
    (tmp_path / "current.json").write_text(
        json.dumps({"version": "champion-v1"}) + "\n",
        encoding="utf-8",
    )

    resolved = resolve_version_dir(tmp_path)

    assert resolved == promoted


def test_resolve_version_fails_closed_without_promoted_pointer(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "candidate-v1")
    _version(tmp_path, "candidate-v2")

    with pytest.raises(
        FileNotFoundError,
        match="refusing to select an unpromoted version by recency",
    ):
        resolve_version_dir(tmp_path)


def test_resolve_version_fails_closed_on_corrupt_pointer(
    tmp_path: Path,
) -> None:
    _version(tmp_path, "candidate-v1")
    (tmp_path / "current.json").write_text(
        '{"version": ',
        encoding="utf-8",
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


def test_pointer_rejects_unsafe_version_reference(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metrics.json").write_text("{}\n", encoding="utf-8")
    (outside / "model.npz").write_bytes(b"model-placeholder")
    (tmp_path / "current.json").write_text(
        json.dumps({"version": "../outside"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        FileNotFoundError,
        match="refusing to select an unpromoted version by recency",
    ):
        resolve_version_dir(tmp_path)


def test_pointer_requires_complete_loadable_version(tmp_path: Path) -> None:
    version = tmp_path / "versions" / "incomplete-v1"
    version.mkdir(parents=True)
    (version / "metrics.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "current.json").write_text(
        json.dumps({"version": "incomplete-v1"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError):
        resolve_version_dir(tmp_path)

