import pytest

from haxlab.models import ImportManifest


def test_import_manifest_serializes_consistent_counts() -> None:
    manifest = ImportManifest(
        replay_count=5,
        unique_replay_count=4,
        duplicate_replay_count=1,
        report_count=3,
        match_count=2,
    )

    value = manifest.as_dict()

    assert value["schema_version"] == 1
    assert value["replay_count"] == 5
    assert value["unique_replay_count"] == 4
    assert value["duplicate_replay_count"] == 1
    assert value["report_count"] == 3
    assert value["match_count"] == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("replay_count", -1),
        ("unique_replay_count", True),
        ("duplicate_replay_count", 1.0),
        ("report_count", "3"),
        ("match_count", None),
    ],
)
def test_import_manifest_rejects_non_native_or_negative_counts(
    field: str, value: object
) -> None:
    kwargs = {
        "replay_count": 1,
        "unique_replay_count": 1,
        "duplicate_replay_count": 0,
        "report_count": 1,
        "match_count": 1,
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match=field):
        ImportManifest(**kwargs).as_dict()


@pytest.mark.parametrize("schema_version", [0, -1, 2, True, 1.0, "1"])
def test_import_manifest_rejects_invalid_schema_version(schema_version: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        ImportManifest(schema_version=schema_version).as_dict()


def test_import_manifest_rejects_unique_count_above_replay_count() -> None:
    manifest = ImportManifest(
        replay_count=2,
        unique_replay_count=3,
        duplicate_replay_count=0,
        report_count=0,
        match_count=0,
    )

    with pytest.raises(ValueError, match="unique_replay_count exceeds replay_count"):
        manifest.as_dict()


def test_import_manifest_rejects_inconsistent_duplicate_count() -> None:
    manifest = ImportManifest(
        replay_count=5,
        unique_replay_count=4,
        duplicate_replay_count=0,
        report_count=0,
        match_count=0,
    )

    with pytest.raises(ValueError, match="duplicate_replay_count"):
        manifest.as_dict()


@pytest.mark.parametrize(
    ("unique_replay_count", "report_count"),
    [
        (1, 2),
        (2, 1),
    ],
)
def test_import_manifest_rejects_match_count_above_available_evidence(
    unique_replay_count: int, report_count: int
) -> None:
    manifest = ImportManifest(
        replay_count=unique_replay_count,
        unique_replay_count=unique_replay_count,
        duplicate_replay_count=0,
        report_count=report_count,
        match_count=2,
    )

    with pytest.raises(ValueError, match="match_count exceeds"):
        manifest.as_dict()


@pytest.mark.parametrize(
    ("unmatched_replays", "unmatched_reports", "match"),
    [
        (["a.hbr2", "b.hbr2"], [], "unmatched_replays"),
        ([], ["1", "2"], "unmatched_reports"),
    ],
)
def test_import_manifest_rejects_unmatched_counts_above_remaining_capacity(
    unmatched_replays: list[str],
    unmatched_reports: list[str],
    match: str,
) -> None:
    manifest = ImportManifest(
        replay_count=2,
        unique_replay_count=2,
        duplicate_replay_count=0,
        report_count=2,
        match_count=1,
        unmatched_replays=unmatched_replays,
        unmatched_reports=unmatched_reports,
    )

    with pytest.raises(ValueError, match=match):
        manifest.as_dict()


def test_import_manifest_allows_failed_replay_to_reduce_unmatched_replay_count() -> None:
    manifest = ImportManifest(
        replay_count=3,
        unique_replay_count=3,
        duplicate_replay_count=0,
        report_count=1,
        match_count=1,
        unmatched_replays=["still-valid-unmatched.hbr2"],
    )

    value = manifest.as_dict()

    assert value["unique_replay_count"] == 3
    assert value["match_count"] == 1
    assert value["unmatched_replays"] == ["still-valid-unmatched.hbr2"]
