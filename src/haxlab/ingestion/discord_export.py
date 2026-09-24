from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from haxlab.models import ImportFailure, MatchReport
from haxlab.ingestion.reports import looks_like_match_report, parse_match_report


def _walk_message_groups(value: Any, inherited_channel_id: str | None = None) -> Iterable[tuple[dict[str, Any], str | None]]:
    if isinstance(value, dict):
        channel = value.get("channel")
        channel_id = inherited_channel_id
        if isinstance(channel, dict) and channel.get("id") is not None:
            channel_id = str(channel["id"])
        elif value.get("channelId") is not None:
            channel_id = str(value["channelId"])

        messages = value.get("messages")
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict):
                    yield message, channel_id

        for key, child in value.items():
            if key != "messages":
                yield from _walk_message_groups(child, channel_id)

    elif isinstance(value, list):
        for child in value:
            yield from _walk_message_groups(child, inherited_channel_id)


def read_discord_exports(root: Path) -> tuple[list[MatchReport], list[ImportFailure]]:
    """Read DiscordChatExporter-like JSON files without depending on one exact schema."""
    reports: list[MatchReport] = []
    failures: list[ImportFailure] = []

    for path in sorted(root.rglob("*.json"), key=lambda p: str(p).casefold()):
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            failures.append(
                ImportFailure(source=str(path), stage="discord_json", error=str(exc))
            )
            continue

        try:
            for message, channel_id in _walk_message_groups(payload):
                if looks_like_match_report(message):
                    reports.append(parse_match_report(message, channel_id=channel_id))
        except Exception as exc:  # keep one malformed export from stopping the inventory
            failures.append(
                ImportFailure(source=str(path), stage="discord_messages", error=str(exc))
            )

    reports.sort(key=lambda report: (report.timestamp or "", report.message_id))
    return reports, failures
