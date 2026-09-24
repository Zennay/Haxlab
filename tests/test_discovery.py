from pathlib import Path

from haxlab.ingestion.discovery import discover_replays


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
