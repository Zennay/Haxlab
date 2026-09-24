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
