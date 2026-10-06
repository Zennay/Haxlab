from __future__ import annotations

import json
from pathlib import Path

from haxlab.ingestion.discord_export import read_discord_exports


def _export_payload() -> dict:
    return {
        "messages": [
            {
                "id": "message-1",
                "content": "MATCH REPORT #m1 Red Team 1 - 0 Blue Team",
                "attachments": [],
            }
        ]
    }


def test_read_discord_exports_reads_regular_json(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    (root / "messages.json").write_text(
        json.dumps(_export_payload()),
        encoding="utf-8",
    )

    reports, failures = read_discord_exports(root)

    assert failures == []
    assert [report.message_id for report in reports] == ["message-1"]


def test_read_discord_exports_ignores_symlink_json(tmp_path: Path) -> None:
    root = tmp_path / "export"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_export_payload()), encoding="utf-8")
    (root / "linked.json").symlink_to(outside)

    reports, failures = read_discord_exports(root)

    assert reports == []
    assert failures == []
