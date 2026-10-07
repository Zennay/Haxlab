from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.ingestion.discovery import discover_replays


def test_discovery_rejects_symlinked_root(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    (actual / "game.hbr2").write_bytes(b"replay")
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)

    with pytest.raises(ValueError, match="symlinked_discovery_root_not_allowed"):
        discover_replays(linked)


def test_discovery_rejects_symlinked_replay_file(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    external = tmp_path / "outside.hbr2"
    external.write_bytes(b"external replay bytes")
    (root / "linked.hbr2").symlink_to(external)

    with pytest.raises(ValueError, match="symlinked_replay_not_allowed"):
        discover_replays(root)


def test_discovery_does_not_recurse_symlinked_directory(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    local = root / "local.HBR2"
    local.write_bytes(b"local replay")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "hidden.hbr2").write_bytes(b"outside replay")
    (root / "linked-dir").symlink_to(outside, target_is_directory=True)

    inventory = discover_replays(root)

    assert [Path(item.path).name for item in inventory.all_files] == ["local.HBR2"]
    assert all("hidden.hbr2" not in item.path for item in inventory.all_files)


def test_discovery_preserves_mixed_case_order_and_content_dedupe(
    tmp_path: Path,
) -> None:
    root = tmp_path / "export"
    (root / "b").mkdir(parents=True)
    (root / "A").mkdir(parents=True)

    first = root / "A" / "Replay.HBR2"
    duplicate = root / "b" / "replay-copy.hbr2"
    other = root / "z.hBr2"
    first.write_bytes(b"same")
    duplicate.write_bytes(b"same")
    other.write_bytes(b"different")

    inventory = discover_replays(root)

    paths = [item.path for item in inventory.all_files]
    assert paths == sorted(paths, key=str.casefold)
    assert len(inventory.all_files) == 3
    assert len(inventory.unique_files) == 2
    assert len(inventory.duplicate_paths_by_hash) == 1
    duplicate_paths = next(iter(inventory.duplicate_paths_by_hash.values()))
    assert duplicate_paths == (str(duplicate),)
