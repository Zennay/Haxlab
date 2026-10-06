from __future__ import annotations

import re
from typing import Any

from haxlab.models import AttachmentRef, MatchReport


_REPORT_ID_RE = re.compile(
    r"MATCH\s+REPORT(?:\s+SCRIM)?\s+#(?P<id>[A-Za-z0-9_.:-]+)",
    re.IGNORECASE,
)
_SCORE_RE = re.compile(
    r"(?:\*\*)?Red\s+Team(?:\*\*)?\s*"
    r"(?P<red>\d+)\s*-\s*(?P<blue>\d+)\s*"
    r"(?:\*\*)?Blue\s+Team(?:\*\*)?",
    re.IGNORECASE,
)
_POSSESSION_RE = re.compile(
    r"Possession:\s*[^\d]*(?P<red>\d+(?:\.\d+)?)%.*?(?P<blue>\d+(?:\.\d+)?)%",
    re.IGNORECASE | re.DOTALL,
)


def _attachment_from_json(value: dict[str, Any]) -> AttachmentRef | None:
    file_name = (
        value.get("fileName")
        or value.get("filename")
        or value.get("name")
        or value.get("file_name")
    )
    if file_name is None:
        return None
    if not isinstance(file_name, str):
        raise ValueError("invalid_attachment_filename")
    file_name = file_name.strip()
    if not file_name:
        return None

    size = value.get("fileSizeBytes")
    if size is None:
        size = value.get("size")
    if size is None:
        size = value.get("size_bytes")
    size_bytes = (
        size
        if isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        else None
    )

    raw_url = value.get("url")
    if raw_url is not None and not isinstance(raw_url, str):
        raise ValueError("invalid_attachment_url")
    url = raw_url.strip() if isinstance(raw_url, str) else None
    if url == "":
        url = None

    return AttachmentRef(
        file_name=file_name,
        url=url,
        size_bytes=size_bytes,
    )


def looks_like_match_report(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    attachments = message.get("attachments") or []
    has_replay = any(
        str(
            item.get("fileName")
            or item.get("filename")
            or item.get("name")
            or ""
        ).lower().endswith(".hbr2")
        for item in attachments
        if isinstance(item, dict)
    )
    return has_replay or "MATCH REPORT" in content.upper() or (
        "RED TEAM" in content.upper() and "BLUE TEAM" in content.upper()
    )


def parse_match_report(
    message: dict[str, Any],
    *,
    channel_id: str | None = None,
) -> MatchReport:
    raw_content = message.get("content")
    if raw_content is None:
        content = ""
    elif not isinstance(raw_content, str):
        raise ValueError("invalid_message_content")
    else:
        content = raw_content

    attachment_values = message.get("attachments")
    if attachment_values is None:
        attachment_values = []
    if not isinstance(attachment_values, list):
        raise ValueError("invalid_attachments")
    if any(not isinstance(item, dict) for item in attachment_values):
        raise ValueError("invalid_attachment")

    attachments = tuple(
        attachment
        for item in attachment_values
        if (attachment := _attachment_from_json(item)) is not None
    )

    report_id_match = _REPORT_ID_RE.search(content)
    score_match = _SCORE_RE.search(content)
    possession_match = _POSSESSION_RE.search(content)
    possession_red = (
        float(possession_match.group("red"))
        if possession_match
        else None
    )
    possession_blue = (
        float(possession_match.group("blue"))
        if possession_match
        else None
    )
    if possession_red is not None and possession_blue is not None:
        if not (
            0.0 <= possession_red <= 100.0
            and 0.0 <= possession_blue <= 100.0
            and abs((possession_red + possession_blue) - 100.0) <= 0.5
        ):
            raise ValueError("invalid_possession_percentages")

    timestamp = (
        message.get("timestamp")
        or message.get("createdAt")
        or message.get("created_at")
    )
    if timestamp is not None and not isinstance(timestamp, str):
        raise ValueError("invalid_timestamp")
    if isinstance(timestamp, str):
        timestamp = timestamp.strip() or None

    raw_message_id = message.get("id") or message.get("messageId")
    if raw_message_id is None or isinstance(raw_message_id, bool):
        raise ValueError("missing_message_id")
    message_id = str(raw_message_id).strip()
    if not message_id:
        raise ValueError("missing_message_id")

    return MatchReport(
        message_id=message_id,
        timestamp=str(timestamp) if timestamp else None,
        channel_id=channel_id,
        content=content,
        attachments=attachments,
        report_id=report_id_match.group("id") if report_id_match else None,
        red_score=int(score_match.group("red")) if score_match else None,
        blue_score=int(score_match.group("blue")) if score_match else None,
        possession_red=possession_red,
        possession_blue=possession_blue,
    )
