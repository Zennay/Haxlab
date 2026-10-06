import json
from pathlib import Path

from haxlab.ingestion.discord_export import read_discord_exports
from haxlab.ingestion.reports import parse_match_report


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



def test_parse_report_rejects_missing_message_id() -> None:
    message = {
        "content": "MATCH REPORT #missing-id Red Team 1 - 0 Blue Team",
        "attachments": [],
    }

    try:
        parse_match_report(message)
    except ValueError as exc:
        assert str(exc) == "missing_message_id"
    else:
        raise AssertionError("expected ValueError")


def test_attachment_size_metadata_is_not_coerced() -> None:
    message = {
        "id": "message-1",
        "content": "MATCH REPORT #m1 Red Team 1 - 0 Blue Team",
        "attachments": [
            {
                "fileName": "replay.hbr2",
                "fileSizeBytes": "44237",
            }
        ],
    }

    report = parse_match_report(message)

    assert report.attachments[0].size_bytes is None


def test_malformed_report_does_not_drop_later_valid_messages(
    tmp_path: Path,
) -> None:
    export = {
        "messages": [
            {
                "content": "MATCH REPORT #bad Red Team 1 - 0 Blue Team",
                "attachments": [],
            },
            {
                "id": "message-2",
                "content": "MATCH REPORT #good Red Team 2 - 1 Blue Team",
                "attachments": [],
            },
        ]
    }
    path = tmp_path / "channel.json"
    path.write_text(json.dumps(export), encoding="utf-8")

    parsed, failures = read_discord_exports(tmp_path)

    assert [report.message_id for report in parsed] == ["message-2"]
    assert len(failures) == 1
    assert failures[0].stage == "discord_message"
    assert "missing_message_id" in failures[0].error



def test_report_metadata_structures_fail_closed() -> None:
    base = {
        "id": "message-1",
        "content": "MATCH REPORT #m1 Red Team 1 - 0 Blue Team",
        "attachments": [],
    }
    cases = [
        ({**base, "content": ["not", "text"]}, "invalid_message_content"),
        ({**base, "attachments": {}}, "invalid_attachments"),
        ({**base, "attachments": ["bad"]}, "invalid_attachment"),
        (
            {
                **base,
                "attachments": [{"fileName": 123}],
            },
            "invalid_attachment_filename",
        ),
        (
            {
                **base,
                "attachments": [
                    {"fileName": "replay.hbr2", "url": {"bad": "url"}}
                ],
            },
            "invalid_attachment_url",
        ),
        ({**base, "timestamp": 123}, "invalid_timestamp"),
    ]

    for message, reason in cases:
        try:
            parse_match_report(message)
        except ValueError as exc:
            assert str(exc) == reason
        else:
            raise AssertionError(f"expected ValueError: {reason}")


def test_attachment_strings_are_trimmed_without_type_coercion() -> None:
    message = {
        "id": "message-1",
        "content": "MATCH REPORT #m1 Red Team 1 - 0 Blue Team",
        "attachments": [
            {
                "fileName": " replay.hbr2 ",
                "url": " https://cdn.discordapp.com/replay ",
                "fileSizeBytes": 44237,
            }
        ],
    }

    report = parse_match_report(message)

    assert report.attachments[0].file_name == "replay.hbr2"
    assert report.attachments[0].url == "https://cdn.discordapp.com/replay"
    assert report.attachments[0].size_bytes == 44237



def test_report_rejects_impossible_possession_percentages() -> None:
    invalid_contents = [
        "MATCH REPORT #m1 Red Team 1 - 0 Blue Team\nPossession: 150% 20%",
        "MATCH REPORT #m1 Red Team 1 - 0 Blue Team\nPossession: 60% 30%",
    ]

    for content in invalid_contents:
        try:
            parse_match_report(
                {
                    "id": "message-1",
                    "content": content,
                    "attachments": [],
                }
            )
        except ValueError as exc:
            assert str(exc) == "invalid_possession_percentages"
        else:
            raise AssertionError("expected ValueError")


def test_report_accepts_rounded_possession_percentages() -> None:
    report = parse_match_report(
        {
            "id": "message-1",
            "content": (
                "MATCH REPORT #m1 Red Team 1 - 0 Blue Team\n"
                "Possession: 50.2% 49.7%"
            ),
            "attachments": [],
        }
    )

    assert report.possession_red == 50.2
    assert report.possession_blue == 49.7
