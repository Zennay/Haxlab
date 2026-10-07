from pathlib import Path

import pytest

from haxlab.runtime.state import RuntimeState


def _event_count(state: RuntimeState) -> int:
    row = state.connection.execute(
        "SELECT COUNT(*) AS count FROM runtime_events"
    ).fetchone()
    return int(row["count"])


@pytest.mark.parametrize(
    "bad_event_type",
    [None, True, 1, 1.0, b"replay_archived", "", " ", " replay_archived", "replay_archived "],
)
def test_event_rejects_malformed_event_type_without_writing(
    tmp_path: Path,
    bad_event_type: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(
            ValueError,
            match="event_type must be a native non-empty string",
        ):
            state.event(bad_event_type)  # type: ignore[arg-type]

        assert _event_count(state) == 0


@pytest.mark.parametrize("bad_subject", [None, True, 1, 1.0, b"sha"])
def test_event_rejects_non_string_subject_without_writing(
    tmp_path: Path,
    bad_subject: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(
            ValueError,
            match="event subject must be a native string",
        ):
            state.event("future_event", subject=bad_subject)  # type: ignore[arg-type]

        assert _event_count(state) == 0


@pytest.mark.parametrize("bad_detail", [None, True, 1, 1.0, b"detail"])
def test_event_rejects_non_string_detail_without_writing(
    tmp_path: Path,
    bad_detail: object,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        with pytest.raises(
            ValueError,
            match="event detail must be a native string",
        ):
            state.event("future_event", detail=bad_detail)  # type: ignore[arg-type]

        assert _event_count(state) == 0


def test_event_keeps_taxonomy_open_and_allows_empty_optional_text(
    tmp_path: Path,
) -> None:
    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.event("future_event")
        row = state.connection.execute(
            """
            SELECT event_type, subject, detail
            FROM runtime_events
            """
        ).fetchone()

    assert dict(row) == {
        "event_type": "future_event",
        "subject": "",
        "detail": "",
    }


def test_event_preserves_multiline_detail_text(tmp_path: Path) -> None:
    detail = "decoder failed:\nline two"

    with RuntimeState(tmp_path / "state.sqlite3") as state:
        state.event(
            "replay_probe_failed",
            subject="a" * 64,
            detail=detail,
        )
        row = state.connection.execute(
            "SELECT detail FROM runtime_events"
        ).fetchone()

    assert row["detail"] == detail
