from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


class DuplicateMatchIdError(ValueError):
    """Raised when canonical M0 match records contain duplicate identities."""


def _require_non_empty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} must be a non-empty canonical string")
    return value


def _provenance_sort_key(
    provenance: tuple[str | None, str | None],
) -> tuple[str, str]:
    replay_sha256, source_message_id = provenance
    return replay_sha256 or "", source_message_id or ""


def validate_unique_match_ids(
    records: Iterable[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Validate and return canonical match IDs in deterministic order.

    This helper is intentionally side-effect free so producer code can validate the
    full candidate publication before writing any M0 artifact. Duplicate diagnostics
    are also deterministic across source ordering: every duplicate identity is
    reported with its complete, sorted provenance set.
    """

    provenance_by_match_id: dict[str, list[tuple[str | None, str | None]]] = {}

    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            raise ValueError(f"canonical match row {index} must be an object")

        match_id = _require_non_empty_string(
            record.get("match_id"), field=f"canonical match row {index} match_id"
        )
        replay_sha256 = record.get("replay_sha256")
        source_message_id = record.get("source_message_id")

        if replay_sha256 is not None:
            replay_sha256 = _require_non_empty_string(
                replay_sha256,
                field=f"canonical match row {index} replay_sha256",
            )
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
        duplicates.sort(key=lambda item: item[0])
        rendered = "; ".join(
            f"{match_id!r} provenances={provenances!r}"
            for match_id, provenances in duplicates
        )
        raise DuplicateMatchIdError(f"duplicate canonical match_id values: {rendered}")

    return tuple(sorted(provenance_by_match_id))
