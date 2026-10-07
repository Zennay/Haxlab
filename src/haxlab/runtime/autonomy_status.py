"""Strict control-evidence parser for the HaxLab autonomy tick."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Mapping


_COUNTER_FIELDS = (
    "processing_pending",
    "analysis_pending",
    "processing_failed",
    "analysis_failed",
    "analysis_ok",
)
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AutonomyStatusError(ValueError):
    """Raised when haxlab-status cannot safely drive autonomy decisions."""


@dataclass(frozen=True)
class AutonomyStatusSnapshot:
    processing_pending: int
    analysis_pending: int
    processing_failed: int
    analysis_failed: int
    analysis_ok: int
    analysis_version: str

    def shell_fields(self) -> str:
        return " ".join(
            (
                str(self.processing_pending),
                str(self.analysis_pending),
                str(self.processing_failed),
                str(self.analysis_failed),
                str(self.analysis_ok),
                self.analysis_version,
            )
        )


def _native_non_negative_int(payload: Mapping[str, Any], field: str) -> int:
    if field not in payload:
        raise AutonomyStatusError(f"missing field: {field}")
    value = payload[field]
    if type(value) is not int or value < 0:
        raise AutonomyStatusError(
            f"{field} must be a native non-negative integer"
        )
    return value


def parse_autonomy_status(payload: object) -> AutonomyStatusSnapshot:
    if type(payload) is not dict:
        raise AutonomyStatusError("status snapshot must be a JSON object")

    counters = {
        field: _native_non_negative_int(payload, field)
        for field in _COUNTER_FIELDS
    }

    if "analysis_version" not in payload:
        raise AutonomyStatusError("missing field: analysis_version")
    version = payload["analysis_version"]
    if type(version) is not str or _VERSION_RE.fullmatch(version) is None:
        raise AutonomyStatusError(
            "analysis_version must be a canonical version token"
        )

    return AutonomyStatusSnapshot(
        processing_pending=counters["processing_pending"],
        analysis_pending=counters["analysis_pending"],
        processing_failed=counters["processing_failed"],
        analysis_failed=counters["analysis_failed"],
        analysis_ok=counters["analysis_ok"],
        analysis_version=version,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        snapshot = parse_autonomy_status(payload)
    except (json.JSONDecodeError, AutonomyStatusError) as exc:
        print(
            json.dumps(
                {
                    "error": "invalid_status_snapshot",
                    "detail": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2

    print(snapshot.shell_fields())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
