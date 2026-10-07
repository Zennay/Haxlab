import json
from pathlib import Path

from haxlab.ingestion.discord_export import read_discord_exports
from haxlab.ingestion.reports import looks_like_match_report, parse_match_report


def test_parses_scrim_report_fields() -> None:
    message = {
        "id": "1552774764110815262",
        "timestamp": "2026-09-24T22:12:27+02:00",
        "content": (
            "📝 MATCH REPORT SCRIM #20260924T221227749-R2\n"
            "**[06:58]** **Red Team** 3 - 2 Blue Team\n"
            "Possession: 🔴 52.34% 🔵 47.66%"
        ),
        "attachments": [
            {
                "fileName": "24-09-26-22h12-ßaaßvsmko.hbr2",
                "url": "https://cdn.discordapp.com/example",
                "fileSizeBytes": 44237,
            }
        ],
    }

    report = parse_match_report(message, channel_id="726932424172371968")

    assert report.report_id == "20260924T221227749-R2"
    assert report.red_score == 3
    assert report.blue_score == 2
    assert report.possession_red == 52.34
    assert report.possession_blue == 47.66
    assert report.attachments[0].file_name.endswith(".hbr2")



def test_preserves_native_zero_byte_attachment_size() -> None:
    report = parse_match_report(
        {
            "id": "zero-size",
            "content": "MATCH REPORT #zero",
            "attachments": [
                {
                    "fileName": "empty.hbr2",
                    "fileSizeBytes": 0,
                }
            ],
        }
    )

    assert len(report.attachments) == 1
    assert report.attachments[0].size_bytes == 0


def test_rejects_coercible_attachment_size() -> None:
    report = parse_match_report(
        {
            "id": "string-size",
            "content": "MATCH REPORT #string-size",
            "attachments": [
                {
                    "fileName": "bad.hbr2",
                    "fileSizeBytes": "44237",
                }
            ],
        }
    )

    assert report.attachments == ()


def test_rejects_non_string_attachment_identity_fields() -> None:
    report = parse_match_report(
        {
            "id": "bad-identity",
            "content": "MATCH REPORT #bad-identity",
            "attachments": [
                {
                    "fileName": {"name": "bad.hbr2"},
                    "fileSizeBytes": 10,
                },
                {
                    "fileName": "bad-url.hbr2",
                    "url": 123,
                    "fileSizeBytes": 10,
                },
            ],
        }
    )

    assert report.attachments == ()


def test_malformed_attachment_does_not_poison_valid_sibling() -> None:
    report = parse_match_report(
        {
            "id": "mixed",
            "content": "MATCH REPORT #mixed",
            "attachments": [
                {
                    "fileName": "bad.hbr2",
                    "fileSizeBytes": -1,
                },
                {
                    "filename": "good.hbr2",
                    "size": 17,
                    "url": "https://cdn.discordapp.com/good",
                },
            ],
        }
    )

    assert len(report.attachments) == 1
    assert report.attachments[0].file_name == "good.hbr2"
    assert report.attachments[0].size_bytes == 17



def test_report_detection_ignores_malformed_attachment_container() -> None:
    assert (
        looks_like_match_report(
            {
                "id": "ordinary",
                "content": "ordinary chat message",
                "attachments": 17,
            }
        )
        is False
    )


def test_report_parser_treats_malformed_attachment_container_as_empty() -> None:
    report = parse_match_report(
        {
            "id": "report-with-bad-container",
            "content": "MATCH REPORT #bad-container",
            "attachments": {"fileName": "not-a-list.hbr2"},
        }
    )

    assert report.report_id == "bad-container"
    assert report.attachments == ()


def test_malformed_attachment_container_does_not_abort_later_export_report(
    tmp_path: Path,
) -> None:
    export = tmp_path / "discord.json"
    export.write_text(
        json.dumps(
            {
                "channel": {"id": "channel-1"},
                "messages": [
                    {
                        "id": "ordinary",
                        "content": "ordinary chat message",
                        "attachments": 17,
                    },
                    {
                        "id": "valid-report",
                        "content": (
                            "MATCH REPORT #later\n"
                            "Red Team 2 - 1 Blue Team\n"
                            "Possession: 51% 49%"
                        ),
                        "attachments": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    reports, failures = read_discord_exports(tmp_path)

    assert failures == []
    assert [report.message_id for report in reports] == ["valid-report"]
    assert reports[0].report_id == "later"
