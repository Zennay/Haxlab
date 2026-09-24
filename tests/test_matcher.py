from haxlab.ingestion.matcher import score_pair
from haxlab.models import AttachmentRef, MatchReport, ReplayFile


def test_matches_dce_hashed_asset_name() -> None:
    replay = ReplayFile(
        path="/tmp/24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
        file_name="24-09-26-22h12-aavsmko-c0ffee1234567890.hbr2",
        size_bytes=44237,
        sha256="a" * 64,
    )
    report = MatchReport(
        message_id="123",
        timestamp="2026-09-24T22:12:27+02:00",
        channel_id="456",
        content="MATCH REPORT",
        attachments=(
            AttachmentRef(
                file_name="24-09-26-22h12-aavsmko.hbr2",
                size_bytes=44237,
            ),
        ),
    )

    score, reasons = score_pair(replay, report)

    assert score >= 0.9
    assert "dce_hashed_asset_filename" in reasons
    assert "attachment_size_matches" in reasons
