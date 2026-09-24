from __future__ import annotations

import re
from typing import Any

from haxlab.models import AttachmentRef, MatchReport


_REPORT_ID_RE = re.compile(
    r"MATCH\s+REPORT(?:\s+SCRIM)?\s+#(?P<id>[A-Za-z0-9_.:-]+)",
    re.IGNORECASE,
)
_SCORE_RE = re.compile(
    r"Red\s+Team\s*(?P<red>\d+)\s*-\s*(?P<blue>\d+)\s*Blue\s+Team",
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
    if not file_name:
        return None

    size = value.get("fileSizeBytes") or value.get("size") or value.get("size_bytes")
    try:
        size_bytes = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_bytes = None

    return AttachmentRef(
        file_name=str(file_name),
        url=str(value.get("url")) if value.get("url") else None,
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
    content = str(message.get("content") or "")
    attachment_values = message.get("attachments") or []
    attachments = tuple(
        attachment
        for item in attachment_values
        if isinstance(item, dict)
        if (attachment := _attachment_from_json(item)) is not None
    )

    report_id_match = _REPORT_ID_RE.search(content)
    score_match = _SCORE_RE.search(content)
    possession_match = _POSSESSION_RE.search(content)

    timestamp = (
        message.get("timestamp")
        or message.get("createdAt")
        or message.get("created_at")
    )

    return MatchReport(
        message_id=str(message.get("id") or message.get("messageId") or ""),
        timestamp=str(timestamp) if timestamp else None,
        channel_id=channel_id,
        content=content,
        attachments=attachments,
        report_id=report_id_match.group("id") if report_id_match else None,
        red_score=int(score_match.group("red")) if score_match else None,
        blue_score=int(score_match.group("blue")) if score_match else None,
        possession_red=float(possession_match.group("red")) if possession_match else None,
        possession_blue=float(possession_match.group("blue")) if possession_match else None,
    )
