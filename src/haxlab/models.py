from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ReplayFile:
    path: str
    file_name: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_path(cls, path: Path, sha256: str) -> "ReplayFile":
        stat = path.stat()
        return cls(
            path=str(path),
            file_name=path.name,
            size_bytes=stat.st_size,
            sha256=sha256,
        )


@dataclass(frozen=True)
class AttachmentRef:
    file_name: str
    url: str | None = None
    size_bytes: int | None = None


@dataclass(frozen=True)
class MatchReport:
    message_id: str
    timestamp: str | None
    channel_id: str | None
    content: str
    attachments: tuple[AttachmentRef, ...] = ()
    report_id: str | None = None
    red_score: int | None = None
    blue_score: int | None = None
    possession_red: float | None = None
    possession_blue: float | None = None


@dataclass(frozen=True)
class MatchCandidate:
    replay_sha256: str
    replay_path: str
    message_id: str | None
    confidence: float
    reasons: tuple[str, ...] = ()


@dataclass
class ImportFailure:
    source: str
    stage: str
    error: str


@dataclass
class ImportManifest:
    schema_version: int = 1
    replay_count: int = 0
    unique_replay_count: int = 0
    duplicate_replay_count: int = 0
    report_count: int = 0
    match_count: int = 0
    unmatched_replays: list[str] = field(default_factory=list)
    unmatched_reports: list[str] = field(default_factory=list)
    failures: list[ImportFailure] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failures"] = [asdict(item) for item in self.failures]
        return data
