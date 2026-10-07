from __future__ import annotations

from collections.abc import Iterable, Mapping
import re
from typing import Any, TypeAlias


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MatchProvenance: TypeAlias = tuple[str, str | None]
MatchIdConflict: TypeAlias = tuple[str, tuple[MatchProvenance, ...]]


def _provenance_sort_key(
    provenance: MatchProvenance,
) -> tuple[str, str]:
    replay_sha256, source_message_id = provenance
    return replay_sha256, source_message_id or ""


class DuplicateMatchIdError(ValueError):
    """Raised when canonical M0 match records contain duplicate identities."""

    def __init__(self, conflicts: tuple[MatchIdConflict, ...]) -> None:
        self.conflicts = tuple(
            sorted(
                (
                    (match_id, tuple(sorted(provenances, key=_provenance_sort_key)))
                    for match_id, provenances in conflicts
                ),
                key=lambda item: item[0],
            )
        )
        rendered = "; ".join(
            f"{match_id!r} provenances={provenances!r}"
            for match_id, provenances in self.conflicts
        )
        super().__init__(f"duplicate canonical match_id values: {rendered}")


def _require_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty canonical string")
    return value


def _require_replay_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase 64-character SHA-256")
    return value


def validate_unique_match_ids(
    records: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Validate and return canonical match IDs in deterministic order.

    This helper is intentionally side-effect free so producer code can validate the
    full candidate publication before writing any M0 artifact. Duplicate diagnostics
    are also deterministic across source ordering: every duplicate identity is
    reported with its complete, sorted provenance set.
    """

    provenance_by_match_id: dict[str, list[MatchProvenance]] = {}

    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"canonical match row {index} must be an object")

        match_id = _require_non_empty_string(
            record.get("match_id"), field=f"canonical match row {index} match_id"
        )
        replay_sha256 = _require_replay_sha256(
            record.get("replay_sha256"),
            field=f"canonical match row {index} replay_sha256",
        )
        source_message_id = record.get("source_message_id")

        if source_message_id is not None:
            source_message_id = _require_non_empty_string(
                source_message_id,
                field=f"canonical match row {index} source_message_id",
            )

        provenance_by_match_id.setdefault(match_id, []).append(
            (replay_sha256, source_message_id)
        )

    duplicates = [
        (match_id, tuple(sorted(provenances, key=_provenance_sort_key)))
        for match_id, provenances in provenance_by_match_id.items()
        if len(provenances) > 1
    ]
    if duplicates:
        raise DuplicateMatchIdError(tuple(duplicates))

    return tuple(sorted(provenance_by_match_id))
