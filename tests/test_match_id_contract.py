from __future__ import annotations

from itertools import permutations
import json
from pathlib import Path
import struct

import pytest

from haxlab.ingestion.match_id_contract import (
    DuplicateMatchIdError,
    derive_canonical_match_id,
    validate_unique_match_ids,
)
from haxlab.ingestion.pipeline import run_import


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

    assert exc.value.conflicts == (
        (
            "league-round-7",
            (("a" * 64, "111"), ("b" * 64, "222")),
        ),
    )

    message = str(exc.value)
    assert "league-round-7" in message
    assert "111" in message
    assert "222" in message
    assert "a" * 64 in message
    assert "b" * 64 in message


def test_duplicate_diagnostics_are_identical_across_source_ordering() -> None:
    rows = [
        _row("league-round-7", "b" * 64, "222"),
        _row("league-round-7", "a" * 64, "111"),
    ]

    messages: list[str] = []
    for candidate in (rows, list(reversed(rows))):
        with pytest.raises(DuplicateMatchIdError) as exc:
            validate_unique_match_ids(candidate)
        messages.append(str(exc.value))

    assert messages[0] == messages[1]
    assert messages[0].index("a" * 64) < messages[0].index("b" * 64)

    expected_conflicts = (
        (
            "league-round-7",
            (("a" * 64, "111"), ("b" * 64, "222")),
        ),
    )
    for candidate in (rows, list(reversed(rows))):
        with pytest.raises(DuplicateMatchIdError) as exc:
            validate_unique_match_ids(candidate)
        assert exc.value.conflicts == expected_conflicts


def test_all_duplicate_identity_groups_are_reported_in_canonical_order() -> None:
    rows = [
        _row("match-z", "d" * 64, "400"),
        _row("match-a", "c" * 64, "300"),
        _row("match-z", "b" * 64, "200"),
        _row("match-a", "a" * 64, "100"),
    ]

    with pytest.raises(DuplicateMatchIdError) as exc:
        validate_unique_match_ids(rows)

    assert exc.value.conflicts == (
        ("match-a", (("a" * 64, "100"), ("c" * 64, "300"))),
        ("match-z", (("b" * 64, "200"), ("d" * 64, "400"))),
    )

    message = str(exc.value)
    assert message.index("'match-a'") < message.index("'match-z'")
    assert message.index("a" * 64) < message.index("c" * 64)
    assert message.index("b" * 64) < message.index("d" * 64)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("match_id", ""),
        ("match_id", " match-1"),
        ("match_id", True),
        ("replay_sha256", ""),
        ("replay_sha256", "A" * 64),
        ("replay_sha256", "a" * 63),
        ("replay_sha256", "g" * 64),
        ("replay_sha256", None),
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


def test_duplicate_fallback_identity_reports_full_replay_provenance() -> None:
    rows = [
        _row("hbr2:deadbeefdeadbeef", "a" * 64, None),
        _row("hbr2:deadbeefdeadbeef", "b" * 64, None),
    ]

    with pytest.raises(DuplicateMatchIdError) as exc:
        validate_unique_match_ids(rows)

    message = str(exc.value)
    assert "hbr2:deadbeefdeadbeef" in message
    assert "a" * 64 in message
    assert "b" * 64 in message
    assert "None" in message


def test_duplicate_error_canonicalizes_machine_readable_conflicts() -> None:
    error = DuplicateMatchIdError(
        (
            ("match-z", (("d" * 64, "400"), ("b" * 64, "200"))),
            ("match-a", (("c" * 64, "300"), ("a" * 64, "100"))),
        )
    )

    assert error.conflicts == (
        ("match-a", (("a" * 64, "100"), ("c" * 64, "300"))),
        ("match-z", (("b" * 64, "200"), ("d" * 64, "400"))),
    )


def test_canonical_match_id_prefers_report_identity() -> None:
    assert (
        derive_canonical_match_id(
            report_id="league-round-7",
            replay_sha256="a" * 64,
        )
        == "league-round-7"
    )


def test_canonical_match_id_preserves_hbr2_sha_prefix_fallback() -> None:
    assert (
        derive_canonical_match_id(
            report_id=None,
            replay_sha256="0123456789abcdef" + "a" * 48,
        )
        == "hbr2:0123456789abcdef"
    )


@pytest.mark.parametrize("report_id", ["", " round-7", "round-7 ", True, 123])
def test_canonical_match_id_rejects_malformed_report_identity(report_id: object) -> None:
    with pytest.raises(ValueError):
        derive_canonical_match_id(
            report_id=report_id,
            replay_sha256="a" * 64,
        )


@pytest.mark.parametrize("replay_sha256", ["", "A" * 64, "a" * 63, "g" * 64, None])
def test_canonical_match_id_rejects_malformed_replay_sha(
    replay_sha256: object,
) -> None:
    with pytest.raises(ValueError):
        derive_canonical_match_id(
            report_id=None,
            replay_sha256=replay_sha256,
        )


def test_duplicate_evidence_is_invariant_across_all_source_permutations() -> None:
    rows = [
        _row("match-x", "c" * 64, "300"),
        _row("match-y", "d" * 64, "400"),
        _row("match-x", "a" * 64, "100"),
        _row("match-x", "b" * 64, "200"),
    ]
    expected_conflicts = (
        (
            "match-x",
            (
                ("a" * 64, "100"),
                ("b" * 64, "200"),
                ("c" * 64, "300"),
            ),
        ),
    )
    messages: set[str] = set()

    for candidate in permutations(rows):
        with pytest.raises(DuplicateMatchIdError) as exc:
            validate_unique_match_ids(candidate)
        assert exc.value.conflicts == expected_conflicts
        messages.add(str(exc.value))

    assert len(messages) == 1


def test_current_importer_matches_canonical_identity_contract(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "derived"
    raw.mkdir()

    replay = raw / "24-09-26-22h12-aavsmko-deadbeefcafebabe.hbr2"
    replay.write_bytes(
        struct.pack(">4sII", b"HBR2", 3, 600) + b"match-id-contract-replay"
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
    (raw / "channel.json").write_text(
        json.dumps(export, ensure_ascii=False),
        encoding="utf-8",
    )

    manifest = run_import(raw, out)
    rows = [
        json.loads(line)
        for line in (out / "matches.jsonl").read_text(encoding="utf-8").splitlines()
    ]

    assert manifest.match_count == 1
    assert len(rows) == 1
    row = rows[0]
    assert row["match_id"] == derive_canonical_match_id(
        report_id=row["report"]["report_id"],
        replay_sha256=row["replay_sha256"],
    )
    assert validate_unique_match_ids(rows) == (row["match_id"],)
