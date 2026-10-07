from __future__ import annotations

import json
from pathlib import Path
import struct

import pytest

from haxlab.ingestion.generation_commit import (
    CURRENT_GENERATION_POINTER_FILE,
    GENERATIONS_DIRECTORY,
)
from haxlab.ingestion.generation_pipeline import (
    GenerationImportError,
    run_generation_import,
)
from haxlab.ingestion.generation_store import (
    PUBLISH_LOCK_FILE,
    resolve_current_generation,
)


def _write_export(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    replay = root / "24-09-26-22h12-aavsmko-deadbeefcafebabe.hbr2"
    replay.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 600)
        + b"generation-pipeline-replay"
    )
    export = {
        "channel": {"id": "726932424172371968"},
        "messages": [
            {
                "id": "1552774764110815262",
                "timestamp": "2026-09-24T22:12:27+02:00",
                "content": (
                    "MATCH REPORT SCRIM #20260924T221227749-R2\n"
                    "Red Team 3 - 2 Blue Team\n"
                    "Possession: 🔴 52.34% 🔵 47.66%"
                ),
                "attachments": [
                    {
                        "fileName": "24-09-26-22h12-aavsmko.hbr2",
                        "fileSizeBytes": replay.stat().st_size,
                    }
                ],
            }
        ],
    }
    (root / "channel.json").write_text(
        json.dumps(export, ensure_ascii=False),
        encoding="utf-8",
    )


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_generation_import_publishes_only_complete_reader_visible_dataset(
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "raw"
    store_root = tmp_path / "store"
    _write_export(export_root)

    result = run_generation_import(export_root, store_root)
    resolved = resolve_current_generation(store_root)

    assert result.manifest.match_count == 1
    assert resolved == result.generation
    rows = [
        json.loads(line)
        for line in (resolved.root / "matches.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [row["match_id"] for row in rows] == ["20260924T221227749-R2"]
    assert (store_root / CURRENT_GENERATION_POINTER_FILE).is_file()
    assert (store_root / GENERATIONS_DIRECTORY).is_dir()
    assert (store_root / PUBLISH_LOCK_FILE).is_file()
    for name in (
        "manifest.json",
        "replays.json",
        "duplicates.json",
        "reports.json",
        "matches.jsonl",
    ):
        assert not (store_root / name).exists()


def test_generation_import_is_idempotent_for_unchanged_export(tmp_path: Path) -> None:
    export_root = tmp_path / "raw"
    store_root = tmp_path / "store"
    _write_export(export_root)

    first = run_generation_import(export_root, store_root)
    second = run_generation_import(export_root, store_root)

    assert second.generation == first.generation
    generations = [
        path
        for path in (store_root / GENERATIONS_DIRECTORY).iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    ]
    assert [path.name for path in generations] == [first.generation.generation_id]


def test_generation_import_does_not_mutate_raw_export(tmp_path: Path) -> None:
    export_root = tmp_path / "raw"
    store_root = tmp_path / "store"
    _write_export(export_root)
    before = _snapshot_tree(export_root)

    run_generation_import(export_root, store_root)

    assert _snapshot_tree(export_root) == before


@pytest.mark.parametrize(
    "relative_store",
    [Path("store"), Path("nested") / "store"],
)
def test_generation_import_rejects_store_inside_raw_before_write(
    tmp_path: Path,
    relative_store: Path,
) -> None:
    export_root = tmp_path / "raw"
    _write_export(export_root)
    before = _snapshot_tree(export_root)
    store_root = export_root / relative_store

    with pytest.raises(GenerationImportError, match="outside the immutable export_root"):
        run_generation_import(export_root, store_root)

    assert _snapshot_tree(export_root) == before
    assert not store_root.exists()


def test_generation_import_ignores_host_tmpdir_inside_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "raw"
    store_root = tmp_path / "store"
    _write_export(export_root)
    before = _snapshot_tree(export_root)
    monkeypatch.setenv("TMPDIR", str(export_root))

    result = run_generation_import(export_root, store_root)

    assert resolve_current_generation(store_root) == result.generation
    assert _snapshot_tree(export_root) == before
    assert not any(
        path.name.startswith(".haxlab-m0-build-")
        for path in export_root.iterdir()
    )


def test_generation_import_rejects_symlink_store_root(tmp_path: Path) -> None:
    export_root = tmp_path / "raw"
    real_store = tmp_path / "real-store"
    linked_store = tmp_path / "linked-store"
    _write_export(export_root)
    real_store.mkdir()
    linked_store.symlink_to(real_store, target_is_directory=True)

    with pytest.raises(GenerationImportError, match="must not be a symlink"):
        run_generation_import(export_root, linked_store)
