from pathlib import Path

import pytest

from haxlab.ingestion.discovery import ReplayInventory, discover_replays


def test_deduplicates_by_content_hash(tmp_path: Path) -> None:
    first = tmp_path / "a.hbr2"
    second = tmp_path / "nested" / "b.hbr2"
    second.parent.mkdir()

    payload = b"HBR2" + b"same-replay-data"
    first.write_bytes(payload)
    second.write_bytes(payload)

    inventory = discover_replays(tmp_path)

    assert len(inventory.all_files) == 2
    assert len(inventory.unique_files) == 1
    assert len(inventory.duplicate_paths_by_hash) == 1


def test_replay_inventory_defensively_freezes_duplicate_path_evidence() -> None:
    source = {"digest-a": ("duplicate-a.hbr2",)}
    inventory = ReplayInventory(
        all_files=(),
        unique_files=(),
        duplicate_paths_by_hash=source,
    )

    source["digest-a"] = ("mutated-after-construction.hbr2",)
    source["digest-b"] = ("new-after-construction.hbr2",)

    assert dict(inventory.duplicate_paths_by_hash) == {
        "digest-a": ("duplicate-a.hbr2",)
    }

    with pytest.raises(TypeError):
        inventory.duplicate_paths_by_hash["digest-a"] = ("mutated.hbr2",)  # type: ignore[index]
