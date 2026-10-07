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


def _first_defined(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _attachment_values(message: dict[str, Any]) -> list[Any]:
    value = message.get("attachments")
    return value if type(value) is list else []


def _attachment_from_json(value: dict[str, Any]) -> AttachmentRef | None:
    file_name = _first_defined(
        value,
        ("fileName", "filename", "name", "file_name"),
    )
    if type(file_name) is not str or not file_name.strip():
        return None

    size = _first_defined(
        value,
        ("fileSizeBytes", "size", "size_bytes"),
    )
    if size is not None and (type(size) is not int or size < 0):
        return None

    raw_url = value.get("url")
    if raw_url is None or raw_url == "":
        url = None
    elif type(raw_url) is str and raw_url.strip():
        url = raw_url
    else:
        return None

    return AttachmentRef(
        file_name=file_name,
        url=url,
        size_bytes=size,
    )


def looks_like_match_report(message: dict[str, Any]) -> bool:
    content = str(message.get("content") or "")
    attachments = _attachment_values(message)
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
    attachment_values = _attachment_values(message)
    attachments = tuple(
        attachment
        for item in attachment_values
        if isinstance(item, dict)
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

    return MatchReport(
        message_id=str(message.get("id") or message.get("messageId") or ""),
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
