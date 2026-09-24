from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from haxlab.models import MatchCandidate, MatchReport, ReplayFile


_ASSET_HASH_RE = re.compile(r"^(?P<stem>.+)-[0-9a-fA-F]{5,64}$")
_REPLAY_TIME_RE = re.compile(
    r"^(?P<day>\d{2})-(?P<month>\d{2})-(?P<year>\d{2,4})-(?P<hour>\d{2})h(?P<minute>\d{2})",
    re.IGNORECASE,
)


def _asset_base_stem(file_name: str) -> str:
    stem = Path(file_name).stem
    match = _ASSET_HASH_RE.match(stem)
    return match.group("stem") if match else stem


def _filename_matches(attachment_name: str, replay_name: str) -> tuple[bool, str]:
    if attachment_name.casefold() == replay_name.casefold():
        return True, "exact_attachment_filename"

    a = Path(attachment_name)
    r = Path(replay_name)
    if a.suffix.casefold() != r.suffix.casefold():
        return False, ""

    if a.stem.casefold() == _asset_base_stem(replay_name).casefold():
        return True, "dce_hashed_asset_filename"

    if r.stem.casefold() == _asset_base_stem(attachment_name).casefold():
        return True, "dce_hashed_attachment_filename"

    return False, ""


def _parse_report_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_replay_time(file_name: str) -> datetime | None:
    match = _REPLAY_TIME_RE.match(file_name)
    if not match:
        return None

    year = int(match.group("year"))
    if year < 100:
        year += 2000

    try:
        return datetime(
            year,
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
        )
    except ValueError:
        return None


def score_pair(replay: ReplayFile, report: MatchReport) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    reasons: list[str] = []

    for attachment in report.attachments:
        matched, reason = _filename_matches(attachment.file_name, replay.file_name)
        if matched:
            score = max(score, 0.84 if reason.startswith("dce_") else 0.90)
            reasons.append(reason)

            if attachment.size_bytes is not None and attachment.size_bytes == replay.size_bytes:
                score += 0.06
                reasons.append("attachment_size_matches")
            break

    replay_time = _parse_replay_time(replay.file_name)
    report_time = _parse_report_time(report.timestamp)
    if replay_time is not None and report_time is not None:
        # Ignore timezone here: filenames are generated in local match time while JSON may
        # contain an offset. Same local date/hour/minute is still useful secondary evidence.
        local_report = report_time.replace(tzinfo=None)
        difference = abs((replay_time - local_report).total_seconds())
        if difference <= 60:
            score += 0.08
            reasons.append("timestamp_within_60s")
        elif difference <= 5 * 60:
            score += 0.03
            reasons.append("timestamp_within_5m")

    return min(score, 1.0), tuple(dict.fromkeys(reasons))


def match_replays_to_reports(
    replays: tuple[ReplayFile, ...] | list[ReplayFile],
    reports: tuple[MatchReport, ...] | list[MatchReport],
    *,
    minimum_confidence: float = 0.65,
) -> list[MatchCandidate]:
    """Greedy one-to-one matching using explicit evidence and confidence."""
    scored: list[tuple[float, str, str, ReplayFile, MatchReport, tuple[str, ...]]] = []

    for replay in replays:
        for report in reports:
            score, reasons = score_pair(replay, report)
            if score >= minimum_confidence:
                scored.append(
                    (score, replay.sha256, report.message_id, replay, report, reasons)
                )

    scored.sort(key=lambda item: (-item[0], item[1], item[2]))

    used_replays: set[str] = set()
    used_reports: set[str] = set()
    matches: list[MatchCandidate] = []

    for score, _, _, replay, report, reasons in scored:
        if replay.sha256 in used_replays or report.message_id in used_reports:
            continue

        used_replays.add(replay.sha256)
        used_reports.add(report.message_id)
        matches.append(
            MatchCandidate(
                replay_sha256=replay.sha256,
                replay_path=replay.path,
                message_id=report.message_id,
                confidence=round(score, 4),
                reasons=reasons,
            )
        )

    matches.sort(key=lambda item: item.replay_sha256)
    return matches
