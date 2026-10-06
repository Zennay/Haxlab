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

    @staticmethod
    def _require_native_non_negative_int(name: str, value: Any) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"invalid import manifest {name}: expected native non-negative int")
        return value

    def _validate(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError(
                "invalid import manifest schema_version: expected exact schema version 1"
            )

        replay_count = self._require_native_non_negative_int(
            "replay_count", self.replay_count
        )
        unique_replay_count = self._require_native_non_negative_int(
            "unique_replay_count", self.unique_replay_count
        )
        duplicate_replay_count = self._require_native_non_negative_int(
            "duplicate_replay_count", self.duplicate_replay_count
        )
        report_count = self._require_native_non_negative_int(
            "report_count", self.report_count
        )
        match_count = self._require_native_non_negative_int(
            "match_count", self.match_count
        )

        if unique_replay_count > replay_count:
            raise ValueError(
                "invalid import manifest counts: unique_replay_count exceeds replay_count"
            )
        if duplicate_replay_count != replay_count - unique_replay_count:
            raise ValueError(
                "invalid import manifest counts: duplicate_replay_count does not "
                "match replay_count - unique_replay_count"
            )
        if match_count > unique_replay_count:
            raise ValueError(
                "invalid import manifest counts: match_count exceeds unique_replay_count"
            )
        if match_count > report_count:
            raise ValueError(
                "invalid import manifest counts: match_count exceeds report_count"
            )

        if len(self.unmatched_replays) > unique_replay_count - match_count:
            raise ValueError(
                "invalid import manifest counts: unmatched_replays exceed remaining "
                "unique replay capacity"
            )
        if len(self.unmatched_reports) > report_count - match_count:
            raise ValueError(
                "invalid import manifest counts: unmatched_reports exceed remaining "
                "report capacity"
            )

    def as_dict(self) -> dict[str, Any]:
        self._validate()
        data = asdict(self)
        data["failures"] = [asdict(item) for item in self.failures]
        return data
