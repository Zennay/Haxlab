from __future__ import annotations

from pathlib import Path

import pytest

from haxlab.runtime.state import RuntimeState, SOURCE_WRITE_STATUSES


def test_source_status_contract_accepts_only_scanner_canonical_values(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        for index, status in enumerate(("archived", "duplicate", "failed")):
            state.mark_seen(
                source_path=f"/incoming/{status}-{index}.hbr2",
                size_bytes=100 + index,
                mtime_ns=1_000 + index,
                sha256=f"{index + 1:064x}",
                status=status,
                error="broken" if status == "failed" else None,
            )

        assert SOURCE_WRITE_STATUSES == frozenset(
            {"archived", "duplicate", "failed"}
        )
        snapshot = state.status_snapshot()
        assert snapshot["source_archived"] == 1
        assert snapshot["source_duplicates"] == 1
        assert snapshot["source_failed"] == 1
        assert snapshot["ingest_integrity_ok"] is False


class _StatusSubclass(str):
    pass


@pytest.mark.parametrize(
    "bad_status",
    [
        "",
        "ARCHIVED",
        "archived ",
        " archived",
        "ok",
        "retry",
        "unknown",
        None,
        True,
        False,
        1,
        0,
        b"archived",
        _StatusSubclass("archived"),
    ],
)
def test_invalid_source_status_first_write_leaves_no_row(
    tmp_path: Path,
    bad_status: object,
) -> None:
    db = tmp_path / "state.sqlite3"

    with RuntimeState(db) as state:
        with pytest.raises(
            ValueError,
            match="source status must be one of: archived, duplicate, failed",
        ):
            state.mark_seen(
                source_path="/incoming/replay.hbr2",
                size_bytes=123,
                mtime_ns=456,
                sha256="a" * 64,
                status=bad_status,  # type: ignore[arg-type]
                error="must not persist",
            )

        assert state.get_source("/incoming/replay.hbr2") is None
        count = state.connection.execute(
            "SELECT COUNT(*) AS count FROM source_files"
        ).fetchone()["count"]
        assert count == 0


@pytest.mark.parametrize(
    "bad_status",
    ["ARCHIVED", "duplicate ", "failed\n", "ok", "retry", True, 1],
)
def test_invalid_source_status_update_cannot_clobber_valid_evidence(
    tmp_path: Path,
    bad_status: object,
) -> None:
    db = tmp_path / "state.sqlite3"
    source_path = "/incoming/replay.hbr2"

    with RuntimeState(db) as state:
        state.mark_seen(
            source_path=source_path,
            size_bytes=123,
            mtime_ns=456,
            sha256="a" * 64,
            status="archived",
            error=None,
        )

        with pytest.raises(
            ValueError,
            match="source status must be one of: archived, duplicate, failed",
        ):
            state.mark_seen(
                source_path=source_path,
                size_bytes=999,
                mtime_ns=999,
                sha256="b" * 64,
                status=bad_status,  # type: ignore[arg-type]
                error="clobber-attempt",
            )

        row = state.connection.execute(
            """
            SELECT source_path, size_bytes, mtime_ns, sha256, status, error
            FROM source_files
            WHERE source_path = ?
            """,
            (source_path,),
        ).fetchone()
        assert dict(row) == {
            "source_path": source_path,
            "size_bytes": 123,
            "mtime_ns": 456,
            "sha256": "a" * 64,
            "status": "archived",
            "error": None,
        }


def test_valid_source_status_update_preserves_existing_upsert_semantics(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    source_path = "/incoming/replay.hbr2"

    with RuntimeState(db) as state:
        state.mark_seen(
            source_path=source_path,
            size_bytes=123,
            mtime_ns=456,
            sha256="a" * 64,
            status="archived",
        )
        state.mark_seen(
            source_path=source_path,
            size_bytes=124,
            mtime_ns=457,
            sha256="a" * 64,
            status="duplicate",
        )

        known = state.get_source(source_path)
        assert known is not None
        assert known.size_bytes == 124
        assert known.mtime_ns == 457
        assert known.sha256 == "a" * 64
        assert known.status == "duplicate"

        snapshot = state.status_snapshot()
        assert snapshot["source_archived"] == 0
        assert snapshot["source_duplicates"] == 1
        assert snapshot["source_failed"] == 0
        assert snapshot["ingest_integrity_ok"] is True
