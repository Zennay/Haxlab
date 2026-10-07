from __future__ import annotations

import pytest

from haxlab.ingestion.match_id_contract import (
    DuplicateMatchIdError,
    validate_unique_match_ids,
)


def _row(
    match_id: str,
    replay_sha256: str,
    source_message_id: str | None,
) -> dict[str, object]:
    return {
        "match_id": match_id,
        "replay_sha256": replay_sha256,
        "source_message_id": source_message_id,
    }


def test_unique_match_ids_are_accepted_deterministically() -> None:
    rows = [
        _row("match-b", "b" * 64, "200"),
        _row("match-a", "a" * 64, "100"),
    ]

    assert validate_unique_match_ids(rows) == ("match-a", "match-b")
    assert validate_unique_match_ids(reversed(rows)) == ("match-a", "match-b")


def test_duplicate_report_identity_fails_closed_with_both_provenances() -> None:
    rows = [
        _row("league-round-7", "a" * 64, "111"),
        _row("league-round-7", "b" * 64, "222"),
    ]

    with pytest.raises(DuplicateMatchIdError) as exc:
        validate_unique_match_ids(rows)

    message = str(exc.value)
    assert "league-round-7" in message
    assert "111" in message
    assert "222" in message
    assert "a" * 64 in message
    assert "b" * 64 in message


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("match_id", ""),
        ("match_id", " match-1"),
        ("match_id", True),
        ("replay_sha256", ""),
        ("replay_sha256", 123),
        ("source_message_id", ""),
        ("source_message_id", 123),
    ],
)
def test_malformed_identity_provenance_fails_closed(field: str, value: object) -> None:
    row = _row("match-1", "a" * 64, "111")
    row[field] = value

    with pytest.raises(ValueError):
        validate_unique_match_ids([row])


def test_non_mapping_rows_fail_closed() -> None:
    with pytest.raises(ValueError):
        validate_unique_match_ids([object()])  # type: ignore[list-item]
