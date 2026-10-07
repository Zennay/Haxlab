from __future__ import annotations

from pathlib import Path

from haxlab.ingestion.discovery import discover_replays
from haxlab.ingestion.discord_export import read_discord_exports
from haxlab.ingestion.source_bundle_receipt import create_source_bundle_receipt


def _write(root: Path, relative: str, payload: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_source_receipt_inventory_exactly_covers_importer_file_selection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "raw-export"
    root.mkdir()

    # Every HBR2 spelling that the replay discovery layer currently accepts.
    _write(root, "replays/top.HBR2", b"replay-top")
    _write(root, "replays/nested/lower.hbr2", b"replay-lower")
    _write(root, "replays/nested/mixed.HbR2", b"replay-mixed")

    # Malformed lowercase JSON makes the Discord reader expose every selected
    # JSON path through deterministic ImportFailure evidence.
    _write(root, "discord/export.json", b"{not-json")
    _write(root, "discord/nested/archive.json", b"[also-not-json")

    # Near misses deliberately stay outside both importer selection and the
    # source receipt. Keeping them in the fixture makes future selector drift
    # fail this contract instead of silently widening raw-input consumption.
    _write(root, "discord/upper.JSON", b"{not-json")
    _write(root, "discord/events.jsonl", b"{not-json")
    _write(root, "replays/legacy.hbr", b"legacy")
    _write(root, "replays/archive.hbr2.gz", b"compressed")
    _write(root, "notes.txt", b"ignored")

    receipt = create_source_bundle_receipt(root)
    replay_inventory = discover_replays(root)
    reports, discord_failures = read_discord_exports(root)

    assert reports == []

    replay_paths = {
        Path(item.path).relative_to(root).as_posix()
        for item in replay_inventory.all_files
    }
    discord_paths = {
        Path(item.source).relative_to(root).as_posix()
        for item in discord_failures
        if item.stage == "discord_json"
    }
    importer_entries = {
        *(("hbr2", path) for path in replay_paths),
        *(("discord_json", path) for path in discord_paths),
    }
    receipt_entries = {
        (item["kind"], item["path"])
        for item in receipt["files"]
    }

    expected_entries = {
        ("hbr2", "replays/top.HBR2"),
        ("hbr2", "replays/nested/lower.hbr2"),
        ("hbr2", "replays/nested/mixed.HbR2"),
        ("discord_json", "discord/export.json"),
        ("discord_json", "discord/nested/archive.json"),
    }

    assert importer_entries == expected_entries
    assert receipt_entries == expected_entries


def test_near_miss_extensions_are_not_hidden_receipt_inputs(tmp_path: Path) -> None:
    root = tmp_path / "raw-export"
    root.mkdir()

    near_misses = {
        "upper.JSON": b"{bad",
        "events.jsonl": b"{bad",
        "legacy.hbr": b"legacy",
        "archive.hbr2.gz": b"compressed",
        "notes.txt": b"ignored",
    }
    for relative, payload in near_misses.items():
        _write(root, relative, payload)

    receipt = create_source_bundle_receipt(root)
    replay_inventory = discover_replays(root)
    reports, failures = read_discord_exports(root)

    assert receipt["files"] == []
    assert replay_inventory.all_files == ()
    assert reports == []
    assert failures == []
