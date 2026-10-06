from haxlab.ingestion.matcher import match_replays_to_reports, score_pair
from haxlab.models import AttachmentRef, MatchReport, ReplayFile


def _replay(*, sha: str, file_name: str, size_bytes: int = 44237) -> ReplayFile:
    return ReplayFile(
        path=f"/tmp/{sha[:8]}/{file_name}",
        file_name=file_name,
        size_bytes=size_bytes,
        sha256=sha,
    )


def _report(
    *,
    message_id: str,
    attachment_name: str,
    size_bytes: int = 44237,
    timestamp: str = "2026-09-24T22:12:27+02:00",
) -> MatchReport:
    return MatchReport(
        message_id=message_id,
        timestamp=timestamp,
        channel_id="456",
        content="MATCH REPORT",
        attachments=(
            AttachmentRef(
                file_name=attachment_name,
                size_bytes=size_bytes,
            ),
        ),
    )


def test_matches_dce_hashed_asset_name() -> None:
    replay = _replay(
        sha="a" * 64,
        file_name="24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
    )
    report = _report(
        message_id="123",
        attachment_name="24-09-26-22h12-aavsmko.hbr2",
    )

    score, reasons = score_pair(replay, report)

    assert score >= 0.9
    assert "dce_hashed_asset_filename" in reasons
    assert "attachment_size_matches" in reasons


def test_ambiguous_reports_are_not_greedily_assigned() -> None:
    file_name = "24-09-26-22h12-aavsmko.hbr2"
    replay = _replay(sha="a" * 64, file_name=file_name)
    reports = [
        _report(message_id="report-a", attachment_name=file_name),
        _report(message_id="report-b", attachment_name=file_name),
    ]

    matches = match_replays_to_reports([replay], reports)

    assert matches == []


def test_ambiguous_replays_are_not_greedily_assigned() -> None:
    file_name = "24-09-26-22h12-aavsmko.hbr2"
    replays = [
        _replay(sha="a" * 64, file_name=file_name),
        _replay(sha="b" * 64, file_name=file_name),
    ]
    report = _report(message_id="report-a", attachment_name=file_name)

    matches = match_replays_to_reports(replays, [report])

    assert matches == []


def test_reciprocal_unique_best_pair_wins_over_weaker_candidate() -> None:
    replay = _replay(
        sha="a" * 64,
        file_name="24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
    )
    exact_report = _report(
        message_id="exact",
        attachment_name="24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
    )
    weaker_report = _report(
        message_id="weaker",
        attachment_name="24-09-26-22h12-aavsmko.hbr2",
    )

    matches = match_replays_to_reports([replay], [weaker_report, exact_report])

    assert len(matches) == 1
    assert matches[0].message_id == "exact"
    assert matches[0].confidence == 1.0


def test_report_without_message_id_is_not_canonical_match_candidate() -> None:
    file_name = "24-09-26-22h12-aavsmko.hbr2"
    replay = _replay(sha="a" * 64, file_name=file_name)
    report = _report(message_id="", attachment_name=file_name)

    assert match_replays_to_reports([replay], [report]) == []
