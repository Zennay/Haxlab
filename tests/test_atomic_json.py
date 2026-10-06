from __future__ import annotations

import json
from pathlib import Path

import pytest

import haxlab.atomic as atomic


def test_atomic_json_replaces_complete_document(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "artifact.json"

    atomic.atomic_json(target, {"schema": "example-v1", "value": 7})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "schema": "example-v1",
        "value": 7,
    }
    assert list(target.parent.glob(f".{target.name}.*.tmp")) == []


def test_atomic_json_preserves_previous_artifact_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "artifact.json"
    target.write_text('{"state":"previous"}\n', encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace interrupted")

    monkeypatch.setattr(atomic.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace interrupted"):
        atomic.atomic_json(target, {"state": "new"})

    assert target.read_text(encoding="utf-8") == '{"state":"previous"}\n'
    assert list(tmp_path.glob(f".{target.name}.*.tmp")) == []
