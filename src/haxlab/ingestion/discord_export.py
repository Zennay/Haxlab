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
    if root.is_symlink():
        raise ValueError("discord_export_root_must_not_be_symlink")
    if not root.exists():
        raise FileNotFoundError(f"discord_export_root_missing:{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"discord_export_root_not_directory:{root}")

    reports_by_id: dict[str, MatchReport] = {}
    message_id_by_report_id: dict[str, str] = {}
    failures: list[ImportFailure] = []

    paths = (
        path
        for path in root.rglob("*.json")
        if path.is_file() and not path.is_symlink()
    )
    for path in sorted(paths, key=lambda p: str(p).casefold()):
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
                if not looks_like_match_report(message):
                    continue
                try:
                    report = parse_match_report(
                        message,
                        channel_id=channel_id,
                    )
                    existing = reports_by_id.get(report.message_id)
                    if existing is not None:
                        if existing != report:
                            failures.append(
                                ImportFailure(
                                    source=(
                                        f"{path}#message:{report.message_id}"
                                    ),
                                    stage="discord_message_duplicate",
                                    error="conflicting_duplicate_message_id",
                                )
                            )
                        continue

                    if report.report_id is not None:
                        report_key = report.report_id.casefold()
                        owner = message_id_by_report_id.get(report_key)
                        if owner is not None and owner != report.message_id:
                            failures.append(
                                ImportFailure(
                                    source=(
                                        f"{path}#message:{report.message_id}"
                                    ),
                                    stage="discord_report_id_duplicate",
                                    error=(
                                        "conflicting_duplicate_report_id:"
                                        f"{report.report_id}"
                                    ),
                                )
                            )
                            continue
                        message_id_by_report_id[report_key] = report.message_id

                    reports_by_id[report.message_id] = report
                except Exception as exc:
                    raw_message_id = message.get("id") or message.get("messageId")
                    message_id = (
                        str(raw_message_id).strip()
                        if raw_message_id is not None
                        else "unknown"
                    )
                    failures.append(
                        ImportFailure(
                            source=f"{path}#message:{message_id or 'unknown'}",
                            stage="discord_message",
                            error=str(exc),
                        )
                    )
        except Exception as exc:
            failures.append(
                ImportFailure(source=str(path), stage="discord_messages", error=str(exc))
            )

    reports = sorted(
        reports_by_id.values(),
        key=lambda report: (report.timestamp or "", report.message_id),
    )
    return reports, failures
