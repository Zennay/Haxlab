from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_string(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
    canonical_whitespace: bool = False,
) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name} must be a native string")
    if not allow_empty and value == "":
        raise ValueError(f"{field_name} must not be empty")
    if canonical_whitespace and value != value.strip():
        raise ValueError(f"{field_name} must not contain surrounding whitespace")
    return value


def _require_optional_string(
    value: object,
    field_name: str,
    *,
    allow_empty: bool = False,
    canonical_whitespace: bool = False,
) -> str | None:
    if value is None:
        return None
    return _require_string(
        value,
        field_name,
        allow_empty=allow_empty,
        canonical_whitespace=canonical_whitespace,
    )


def _require_nonnegative_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be a native integer")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


def _require_optional_nonnegative_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _require_nonnegative_int(value, field_name)


def _require_finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{field_name} must be a native number")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{field_name} must be finite")
    if not minimum <= numeric <= maximum:
        raise ValueError(f"{field_name} must be in [{minimum}, {maximum}]")
    return numeric


def _require_optional_finite_number(
    value: object,
    field_name: str,
    *,
    minimum: float,
    maximum: float,
) -> float | None:
    if value is None:
        return None
    return _require_finite_number(
        value,
        field_name,
        minimum=minimum,
        maximum=maximum,
    )


def _require_sha256(value: object, field_name: str) -> str:
    digest = _require_string(value, field_name)
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError(f"{field_name} must be a lowercase 64-character SHA-256")
    return digest


def _require_string_tuple(
    value: object,
    field_name: str,
    *,
    unique: bool = False,
) -> tuple[str, ...]:
    if type(value) is not tuple:
        raise TypeError(f"{field_name} must be a tuple")
    checked: list[str] = []
    for index, item in enumerate(value):
        checked.append(
            _require_string(
                item,
                f"{field_name}[{index}]",
                canonical_whitespace=True,
            )
        )
    if unique and len(set(checked)) != len(checked):
        raise ValueError(f"{field_name} must not contain duplicate values")
    return tuple(checked)


@dataclass(frozen=True)
class ReplayFile:
    path: str
    file_name: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        path = _require_string(self.path, "ReplayFile.path")
        file_name = _require_string(self.file_name, "ReplayFile.file_name")
        if Path(path).name != file_name:
            raise ValueError(
                "ReplayFile.file_name must equal the basename of ReplayFile.path"
            )
        _require_nonnegative_int(self.size_bytes, "ReplayFile.size_bytes")
        _require_sha256(self.sha256, "ReplayFile.sha256")

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

    def __post_init__(self) -> None:
        _require_string(self.file_name, "AttachmentRef.file_name")
        _require_optional_string(self.url, "AttachmentRef.url")
        _require_optional_nonnegative_int(
            self.size_bytes,
            "AttachmentRef.size_bytes",
        )


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

    def __post_init__(self) -> None:
        _require_string(
            self.message_id,
            "MatchReport.message_id",
            canonical_whitespace=True,
        )
        _require_optional_string(
            self.timestamp,
            "MatchReport.timestamp",
            canonical_whitespace=True,
        )
        _require_optional_string(
            self.channel_id,
            "MatchReport.channel_id",
            canonical_whitespace=True,
        )
        _require_string(self.content, "MatchReport.content", allow_empty=True)

        if type(self.attachments) is not tuple:
            raise TypeError("MatchReport.attachments must be a tuple")
        if any(type(item) is not AttachmentRef for item in self.attachments):
            raise TypeError("MatchReport.attachments must contain AttachmentRef values")

        _require_optional_string(
            self.report_id,
            "MatchReport.report_id",
            canonical_whitespace=True,
        )
        _require_optional_nonnegative_int(self.red_score, "MatchReport.red_score")
        _require_optional_nonnegative_int(self.blue_score, "MatchReport.blue_score")
        if (self.red_score is None) != (self.blue_score is None):
            raise ValueError(
                "MatchReport red_score and blue_score must be both present or both absent"
            )

        _require_optional_finite_number(
            self.possession_red,
            "MatchReport.possession_red",
            minimum=0.0,
            maximum=100.0,
        )
        _require_optional_finite_number(
            self.possession_blue,
            "MatchReport.possession_blue",
            minimum=0.0,
            maximum=100.0,
        )
        if (self.possession_red is None) != (self.possession_blue is None):
            raise ValueError(
                "MatchReport possession_red and possession_blue must be both present or both absent"
            )


@dataclass(frozen=True)
class MatchCandidate:
    replay_sha256: str
    replay_path: str
    message_id: str | None
    confidence: float
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_sha256(self.replay_sha256, "MatchCandidate.replay_sha256")
        _require_string(self.replay_path, "MatchCandidate.replay_path")
        _require_optional_string(
            self.message_id,
            "MatchCandidate.message_id",
            canonical_whitespace=True,
        )
        _require_finite_number(
            self.confidence,
            "MatchCandidate.confidence",
            minimum=0.0,
            maximum=1.0,
        )
        _require_string_tuple(
            self.reasons,
            "MatchCandidate.reasons",
            unique=True,
        )


@dataclass
class ImportFailure:
    source: str
    stage: str
    error: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        _require_string(self.source, "ImportFailure.source")
        _require_string(
            self.stage,
            "ImportFailure.stage",
            canonical_whitespace=True,
        )
        _require_string(self.error, "ImportFailure.error")


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

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if type(self.schema_version) is not int:
            raise TypeError("ImportManifest.schema_version must be a native integer")
        if self.schema_version != 1:
            raise ValueError("ImportManifest.schema_version must equal 1")

        replay_count = _require_nonnegative_int(
            self.replay_count,
            "ImportManifest.replay_count",
        )
        unique_replay_count = _require_nonnegative_int(
            self.unique_replay_count,
            "ImportManifest.unique_replay_count",
        )
        duplicate_replay_count = _require_nonnegative_int(
            self.duplicate_replay_count,
            "ImportManifest.duplicate_replay_count",
        )
        report_count = _require_nonnegative_int(
            self.report_count,
            "ImportManifest.report_count",
        )
        match_count = _require_nonnegative_int(
            self.match_count,
            "ImportManifest.match_count",
        )

        if replay_count != unique_replay_count + duplicate_replay_count:
            raise ValueError(
                "ImportManifest replay counts must satisfy "
                "replay_count == unique_replay_count + duplicate_replay_count"
            )
        if match_count > unique_replay_count:
            raise ValueError(
                "ImportManifest.match_count must not exceed unique_replay_count"
            )
        if match_count > report_count:
            raise ValueError(
                "ImportManifest.match_count must not exceed report_count"
            )

        self._validate_string_list(
            self.unmatched_replays,
            "ImportManifest.unmatched_replays",
        )
        self._validate_string_list(
            self.unmatched_reports,
            "ImportManifest.unmatched_reports",
        )

        expected_unmatched_reports = report_count - match_count
        if len(self.unmatched_reports) != expected_unmatched_reports:
            raise ValueError(
                "ImportManifest.unmatched_reports length must equal "
                "report_count - match_count"
            )

        maximum_unmatched_replays = unique_replay_count - match_count
        if len(self.unmatched_replays) > maximum_unmatched_replays:
            raise ValueError(
                "ImportManifest.unmatched_replays length must not exceed "
                "unique_replay_count - match_count"
            )

        if type(self.failures) is not list:
            raise TypeError("ImportManifest.failures must be a list")
        for index, failure in enumerate(self.failures):
            if type(failure) is not ImportFailure:
                raise TypeError(
                    f"ImportManifest.failures[{index}] must be an ImportFailure"
                )
            failure.validate()

    @staticmethod
    def _validate_string_list(value: object, field_name: str) -> None:
        if type(value) is not list:
            raise TypeError(f"{field_name} must be a list")
        checked: list[str] = []
        for index, item in enumerate(value):
            checked.append(
                _require_string(
                    item,
                    f"{field_name}[{index}]",
                    canonical_whitespace=True,
                )
            )
        if len(set(checked)) != len(checked):
            raise ValueError(f"{field_name} must not contain duplicate values")

    def as_dict(self) -> dict[str, Any]:
        self.validate()
        data = asdict(self)
        data["failures"] = [asdict(item) for item in self.failures]
        return data
