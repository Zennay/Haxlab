from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.ingestion.dataset_receipt import M0_ARTIFACTS
from haxlab.ingestion.generation_commit import (
    CURRENT_GENERATION_POINTER_FILE,
    GENERATION_COMMIT_FILE,
    GENERATIONS_DIRECTORY,
)
from haxlab.ingestion.generation_store import (
    GenerationStoreError,
    PUBLICATION_STAGES,
    publish_generation,
    resolve_current_generation,
)


class InjectedCrash(RuntimeError):
    pass


def _write_dataset(root: Path, *, match_id: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": (f'{{"match_count":1,"id":"{match_id}"}}\n').encode(),
        "replays.json": b'[{"sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}]\n',
        "duplicates.json": b'[]\n',
        "reports.json": b'[]\n',
        "matches.jsonl": (f'{{"match_id":"{match_id}"}}\n').encode(),
    }
    for name in M0_ARTIFACTS:
        (root / name).write_bytes(payloads[name])


def _fault_at(target: str):
    def fault(stage: str) -> None:
        if stage == target:
            raise InjectedCrash(stage)

    return fault


def test_publish_and_resolve_complete_generation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")

    published = publish_generation(source, store)
    resolved = resolve_current_generation(store)

    assert resolved == published
    assert resolved.root.is_dir()
    assert resolved.root.parent.name == GENERATIONS_DIRECTORY
    assert (resolved.root / GENERATION_COMMIT_FILE).is_file()
    assert (store / CURRENT_GENERATION_POINTER_FILE).is_file()
    for name in M0_ARTIFACTS:
        assert (resolved.root / name).read_bytes() == (source / name).read_bytes()


def test_second_generation_keeps_first_immutable(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    first = publish_generation(source, store)
    first_bytes = {name: (first.root / name).read_bytes() for name in M0_ARTIFACTS}

    _write_dataset(source, match_id="m-2")
    second = publish_generation(source, store)

    assert second.generation_id != first.generation_id
    assert resolve_current_generation(store) == second
    assert first.root.is_dir()
    for name in M0_ARTIFACTS:
        assert (first.root / name).read_bytes() == first_bytes[name]


def test_republishing_same_generation_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")

    first = publish_generation(source, store)
    second = publish_generation(source, store)

    assert second == first
    entries = [
        path.name
        for path in (store / GENERATIONS_DIRECTORY).iterdir()
        if not path.name.startswith(".staging-")
    ]
    assert entries == [first.generation_id]


@pytest.mark.parametrize(
    "stage",
    [
        "after_stage_artifacts",
        "after_stage_commit",
        "after_generation_publish",
        "after_pointer_temp",
    ],
)
def test_failure_before_pointer_swap_preserves_previous_generation(
    tmp_path: Path,
    stage: str,
) -> None:
    assert stage in PUBLICATION_STAGES
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="old")
    previous = publish_generation(source, store)

    _write_dataset(source, match_id="new")
    with pytest.raises(InjectedCrash, match=stage):
        publish_generation(source, store, _fault=_fault_at(stage))

    assert resolve_current_generation(store) == previous


def test_failure_after_pointer_swap_exposes_new_complete_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="old")
    previous = publish_generation(source, store)

    _write_dataset(source, match_id="new")
    with pytest.raises(InjectedCrash, match="after_pointer_swap"):
        publish_generation(
            source,
            store,
            _fault=_fault_at("after_pointer_swap"),
        )

    current = resolve_current_generation(store)
    assert current.generation_id != previous.generation_id
    assert b'"match_id":"new"' in (current.root / "matches.jsonl").read_bytes()


def test_staging_failures_do_not_leave_staging_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="old")
    publish_generation(source, store)

    _write_dataset(source, match_id="new")
    with pytest.raises(InjectedCrash):
        publish_generation(
            source,
            store,
            _fault=_fault_at("after_stage_commit"),
        )

    assert not any(
        path.name.startswith(".staging-")
        for path in (store / GENERATIONS_DIRECTORY).iterdir()
    )


def test_orphan_complete_generation_is_not_visible_without_pointer_swap(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="old")
    previous = publish_generation(source, store)

    _write_dataset(source, match_id="new")
    with pytest.raises(InjectedCrash):
        publish_generation(
            source,
            store,
            _fault=_fault_at("after_generation_publish"),
        )

    complete_generations = [
        path
        for path in (store / GENERATIONS_DIRECTORY).iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    ]
    assert len(complete_generations) == 2
    assert resolve_current_generation(store) == previous


def test_reader_fails_closed_on_artifact_tamper(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    current = publish_generation(source, store)

    with (current.root / "matches.jsonl").open("ab") as handle:
        handle.write(b'{"match_id":"tampered"}\n')

    with pytest.raises(
        GenerationStoreError,
        match="resolved generation artifacts do not match commit",
    ):
        resolve_current_generation(store)


def test_reader_rejects_symlink_current_pointer(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    publish_generation(source, store)

    pointer = store / CURRENT_GENERATION_POINTER_FILE
    backup = store / "pointer-backup.json"
    pointer.replace(backup)
    pointer.symlink_to(backup.name)

    with pytest.raises(GenerationStoreError, match="unsafe or unreadable file"):
        resolve_current_generation(store)


def test_reader_rejects_symlink_generation_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    current = publish_generation(source, store)

    real = current.root.with_name(current.root.name + "-real")
    current.root.rename(real)
    current.root.symlink_to(real.name, target_is_directory=True)

    with pytest.raises(GenerationStoreError, match="unsafe or unreadable directory entry"):
        resolve_current_generation(store)


def test_publish_rejects_symlink_store_root(tmp_path: Path) -> None:
    source = tmp_path / "source"
    real_store = tmp_path / "real-store"
    linked_store = tmp_path / "linked-store"
    _write_dataset(source, match_id="m-1")
    real_store.mkdir()
    linked_store.symlink_to(real_store, target_is_directory=True)

    with pytest.raises(GenerationStoreError, match="unsafe or unreadable directory"):
        publish_generation(source, linked_store)


def test_republish_does_not_overwrite_corrupted_existing_generation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    current = publish_generation(source, store)
    (current.root / GENERATION_COMMIT_FILE).write_bytes(b"{}\n")

    with pytest.raises(
        GenerationStoreError,
        match="existing generation evidence is invalid",
    ):
        publish_generation(source, store)
    assert (current.root / GENERATION_COMMIT_FILE).read_bytes() == b"{}\n"


def test_publish_never_mutates_source_dataset(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    before = {
        path.name: path.read_bytes()
        for path in source.iterdir()
    }

    publish_generation(source, store)

    after = {
        path.name: path.read_bytes()
        for path in source.iterdir()
    }
    assert after == before
    assert set(after) == set(M0_ARTIFACTS)


@pytest.mark.parametrize(
    "store_relative",
    [
        Path("."),
        Path("nested-store"),
        Path("nested") / "store",
    ],
)
def test_publish_rejects_store_inside_source_before_mutation(
    tmp_path: Path,
    store_relative: Path,
) -> None:
    source = tmp_path / "source"
    _write_dataset(source, match_id="m-1")
    before_names = sorted(path.name for path in source.iterdir())
    store = source if store_relative == Path(".") else source / store_relative

    with pytest.raises(GenerationStoreError, match="outside source_root"):
        publish_generation(source, store)

    assert sorted(path.name for path in source.iterdir()) == before_names
    if store != source:
        assert not store.exists()


def test_pointer_temp_is_cleaned_when_swap_is_not_reached(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="old")
    publish_generation(source, store)

    _write_dataset(source, match_id="new")
    with pytest.raises(InjectedCrash, match="after_pointer_temp"):
        publish_generation(
            source,
            store,
            _fault=_fault_at("after_pointer_temp"),
        )

    assert not any(
        path.name.startswith(f".{CURRENT_GENERATION_POINTER_FILE}.")
        and path.name.endswith(".tmp")
        for path in store.iterdir()
    )


def test_reader_ignores_unreferenced_staging_directory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    store = tmp_path / "store"
    _write_dataset(source, match_id="m-1")
    current = publish_generation(source, store)

    stale = store / GENERATIONS_DIRECTORY / ".staging-unreferenced"
    stale.mkdir()
    (stale / "junk").write_text("partial", encoding="utf-8")

    assert resolve_current_generation(store) == current
