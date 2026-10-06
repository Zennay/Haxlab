from pathlib import Path

from haxlab.hashing import sha256_file
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



def test_discovery_ignores_symlink_replays(tmp_path: Path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"HBR2" + b"outside-source")
    link = tmp_path / "linked.hbr2"
    link.symlink_to(source)

    inventory = discover_replays(tmp_path)

    assert inventory.all_files == ()
    assert inventory.unique_files == ()
    assert inventory.duplicate_paths_by_hash == {}


def test_sha256_file_rejects_non_positive_or_non_integer_chunk_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sample.hbr2"
    path.write_bytes(b"HBR2-content")

    for chunk_size in (0, -1, True, 1.5):
        try:
            sha256_file(path, chunk_size=chunk_size)
        except ValueError as exc:
            assert "positive integer" in str(exc)
        else:
            raise AssertionError("expected ValueError")



def test_discovery_rejects_source_changed_during_hash(
    tmp_path: Path,
    monkeypatch,
) -> None:
    replay = tmp_path / "changing.hbr2"
    replay.write_bytes(b"HBR2-before")

    from haxlab.ingestion import discovery as discovery_module

    real_hash = discovery_module.sha256_file

    def hash_then_mutate(path: Path) -> str:
        digest = real_hash(path)
        path.write_bytes(b"HBR2-after-with-different-size")
        return digest

    monkeypatch.setattr(
        discovery_module,
        "sha256_file",
        hash_then_mutate,
    )

    try:
        discover_replays(tmp_path)
    except RuntimeError as exc:
        assert "source_changed_during_hash" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")



def test_discovery_rejects_invalid_roots(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    try:
        discover_replays(missing)
    except FileNotFoundError as exc:
        assert "replay_root_missing" in str(exc)
    else:
        raise AssertionError("expected FileNotFoundError")

    regular_file = tmp_path / "not-a-directory"
    regular_file.write_text("x", encoding="utf-8")
    try:
        discover_replays(regular_file)
    except NotADirectoryError as exc:
        assert "replay_root_not_directory" in str(exc)
    else:
        raise AssertionError("expected NotADirectoryError")

    target = tmp_path / "target"
    target.mkdir()
    symlink_root = tmp_path / "linked-root"
    symlink_root.symlink_to(target, target_is_directory=True)
    try:
        discover_replays(symlink_root)
    except ValueError as exc:
        assert str(exc) == "replay_root_must_not_be_symlink"
    else:
        raise AssertionError("expected ValueError")
