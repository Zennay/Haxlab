from __future__ import annotations

import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haxlab.evaluation.models import EvaluationEvidence, PromotionPolicy, Regression


_NATIVE_PATH_TYPE = type(Path("."))


_ALLOWED_KEYS = {
    "challenger_id",
    "champion_id",
    "games_vs_champion",
    "score_rate_vs_champion",
    "score_rate_lower_bound",
    "goal_difference_per_game",
    "frozen_scenarios_total",
    "frozen_scenarios_passed",
    "regressions",
    "reproducible",
}

_REQUIRED_KEYS = _ALLOWED_KEYS - {"goal_difference_per_game"}


class _DuplicateJsonKeyError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError(key)
        result[key] = value
    return result


def _read_stable_regular_text(path: Path) -> str:
    """Read one stable regular file without following a replaced final symlink."""
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise OSError("evidence_path_not_regular")

    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise OSError("evidence_opened_not_regular")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("evidence_path_identity_changed_before_open")

        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)

        after_read = os.fstat(fd)
    finally:
        os.close(fd)

    after_path = os.lstat(path)
    if not stat.S_ISREG(after_path.st_mode):
        raise OSError("evidence_path_not_regular_after_read")
    if (after_path.st_dev, after_path.st_ino) != (
        opened.st_dev,
        opened.st_ino,
    ):
        raise OSError("evidence_path_identity_changed_after_read")

    snapshot_before = (
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
    )
    snapshot_after_read = (
        after_read.st_size,
        after_read.st_mtime_ns,
        after_read.st_ctime_ns,
    )
    snapshot_after_path = (
        after_path.st_size,
        after_path.st_mtime_ns,
        after_path.st_ctime_ns,
    )
    if (
        snapshot_before != snapshot_after_read
        or snapshot_before != snapshot_after_path
    ):
        raise OSError("evidence_file_changed_during_read")

    payload = b"".join(chunks)
    if len(payload) != opened.st_size:
        raise OSError("evidence_file_size_changed_during_read")
    return payload.decode("utf-8")


def _load_json(path: Path) -> Any:
    return json.loads(
        _read_stable_regular_text(path),
        object_pairs_hook=_unique_json_object,
    )


@dataclass(frozen=True)
class EvidenceParseResult:
    evidence: EvaluationEvidence | None
    reasons: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.evidence is not None and not self.reasons


def _native_non_empty_string(
    failures: list[str],
    value: Any,
    label: str,
) -> str:
    if type(value) is not str:
        failures.append(f"invalid_evidence:{label}:not_string")
        return ""
    if not value.strip():
        failures.append(f"invalid_evidence:{label}:empty")
        return ""
    if value != value.strip():
        failures.append(f"invalid_evidence:{label}:surrounding_whitespace")
        return ""
    return value


def _native_integer(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: int = 0,
) -> int:
    if type(value) is not int:
        failures.append(f"invalid_evidence:{label}:not_integer")
        return 0
    if value < minimum:
        failures.append(
            f"invalid_evidence:{label}:below_minimum:{value}<{minimum}"
        )
    return value


def _native_number(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) not in (int, float):
        failures.append(f"invalid_evidence:{label}:not_number")
        return 0.0
    number = float(value)
    if not math.isfinite(number):
        failures.append(f"invalid_evidence:{label}:non_finite")
        return 0.0
    if minimum is not None and number < minimum:
        failures.append(
            f"invalid_evidence:{label}:below_minimum:"
            f"{number:.6f}<{minimum:.6f}"
        )
    if maximum is not None and number > maximum:
        failures.append(
            f"invalid_evidence:{label}:above_maximum:"
            f"{number:.6f}>{maximum:.6f}"
        )
    return number


def _parse_regressions(
    failures: list[str],
    value: Any,
) -> tuple[Regression, ...]:
    if type(value) is not list:
        failures.append("invalid_evidence:regressions:not_list")
        return ()

    parsed: list[Regression] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(value):
        label = f"regressions:{index}"
        if type(raw) is not dict:
            failures.append(f"invalid_evidence:{label}:not_object")
            continue

        expected = {"scenario", "severity", "details"}
        raw_keys = {key for key in raw if type(key) is str}
        if len(raw_keys) != len(raw):
            failures.append(f"invalid_evidence:{label}:non_string_key")
        missing = sorted(expected - raw_keys)
        unexpected = sorted(raw_keys - expected)
        for key in missing:
            failures.append(f"invalid_evidence:{label}:missing:{key}")
        for key in unexpected:
            failures.append(f"invalid_evidence:{label}:unexpected:{key}")

        scenario = _native_non_empty_string(
            failures, raw.get("scenario"), f"{label}:scenario"
        )
        severity = _native_non_empty_string(
            failures, raw.get("severity"), f"{label}:severity"
        )
        details = raw.get("details", "")
        if type(details) is not str:
            failures.append(f"invalid_evidence:{label}:details:not_string")
            details = ""

        identity = (scenario, severity, details)
        if scenario and severity and identity in seen:
            failures.append(f"invalid_evidence:{label}:duplicate")
        seen.add(identity)

        parsed.append(
            Regression(
                scenario=scenario,
                severity=severity,
                details=details,
            )
        )
    return tuple(parsed)


def parse_evaluation_evidence(payload: Any) -> EvidenceParseResult:
    """Parse serialized promotion evidence without type coercion."""
    if type(payload) is not dict:
        return EvidenceParseResult(
            evidence=None,
            reasons=("invalid_evidence:payload:not_object",),
        )

    failures: list[str] = []
    keys = {key for key in payload if type(key) is str}
    if len(keys) != len(payload):
        failures.append("invalid_evidence:payload:non_string_key")
    for key in sorted(_REQUIRED_KEYS - keys):
        failures.append(f"invalid_evidence:missing:{key}")
    for key in sorted(keys - _ALLOWED_KEYS):
        failures.append(f"invalid_evidence:unexpected:{key}")

    challenger_id = _native_non_empty_string(
        failures, payload.get("challenger_id"), "challenger_id"
    )
    champion_id = _native_non_empty_string(
        failures, payload.get("champion_id"), "champion_id"
    )
    if challenger_id and champion_id and challenger_id == champion_id:
        failures.append("invalid_evidence:challenger_matches_champion")

    games = _native_integer(
        failures, payload.get("games_vs_champion"), "games_vs_champion"
    )
    score_rate = _native_number(
        failures,
        payload.get("score_rate_vs_champion"),
        "score_rate_vs_champion",
        minimum=0.0,
        maximum=1.0,
    )
    lower_bound = _native_number(
        failures,
        payload.get("score_rate_lower_bound"),
        "score_rate_lower_bound",
        minimum=0.0,
        maximum=1.0,
    )
    if lower_bound > score_rate:
        failures.append(
            "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"
        )

    goal_difference_raw = payload.get("goal_difference_per_game")
    goal_difference: float | None
    if goal_difference_raw is None:
        goal_difference = None
    else:
        goal_difference = _native_number(
            failures,
            goal_difference_raw,
            "goal_difference_per_game",
        )

    scenario_total = _native_integer(
        failures,
        payload.get("frozen_scenarios_total"),
        "frozen_scenarios_total",
    )
    scenario_passed = _native_integer(
        failures,
        payload.get("frozen_scenarios_passed"),
        "frozen_scenarios_passed",
    )
    if scenario_passed > scenario_total:
        failures.append(
            "invalid_evidence:frozen_scenarios_passed_exceeds_total:"
            f"{scenario_passed}>{scenario_total}"
        )

    regressions = _parse_regressions(
        failures, payload.get("regressions")
    )

    reproducible = payload.get("reproducible")
    if type(reproducible) is not bool:
        failures.append("invalid_evidence:reproducible:not_boolean")
        reproducible = False

    if failures:
        return EvidenceParseResult(
            evidence=None,
            reasons=tuple(failures),
        )

    return EvidenceParseResult(
        evidence=EvaluationEvidence(
            challenger_id=challenger_id,
            champion_id=champion_id,
            games_vs_champion=games,
            score_rate_vs_champion=score_rate,
            score_rate_lower_bound=lower_bound,
            goal_difference_per_game=goal_difference,
            frozen_scenarios_total=scenario_total,
            frozen_scenarios_passed=scenario_passed,
            regressions=regressions,
            reproducible=reproducible,
        ),
        reasons=(),
    )


def load_evaluation_evidence(path: Path) -> EvidenceParseResult:
    """Load one regular JSON file and parse it with the strict contract."""
    if type(path) is not _NATIVE_PATH_TYPE:
        return EvidenceParseResult(
            evidence=None,
            reasons=("invalid_evidence:path:not_path",),
        )
    if path.is_symlink():
        return EvidenceParseResult(
            evidence=None,
            reasons=("invalid_evidence:path:symlink",),
        )
    if not path.is_file():
        return EvidenceParseResult(
            evidence=None,
            reasons=("invalid_evidence:path:not_file",),
        )

    try:
        payload = _load_json(path)
    except _DuplicateJsonKeyError as exc:
        return EvidenceParseResult(
            evidence=None,
            reasons=(f"invalid_evidence:path:duplicate_json_key:{exc.key}",),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return EvidenceParseResult(
            evidence=None,
            reasons=("invalid_evidence:path:unreadable_or_invalid_json",),
        )

    return parse_evaluation_evidence(payload)


@dataclass(frozen=True)
class PolicyParseResult:
    policy: PromotionPolicy | None
    reasons: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.policy is not None and not self.reasons


def _policy_integer(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: int,
) -> int:
    if type(value) is not int:
        failures.append(f"invalid_policy:{label}:not_integer")
        return minimum
    if value < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:{value}<{minimum}"
        )
    return value


def _policy_number(
    failures: list[str],
    value: Any,
    label: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if type(value) not in (int, float):
        failures.append(f"invalid_policy:{label}:not_number")
        return minimum
    number = float(value)
    if not math.isfinite(number):
        failures.append(f"invalid_policy:{label}:non_finite")
        return minimum
    if number < minimum:
        failures.append(
            f"invalid_policy:{label}:below_minimum:"
            f"{number:.6f}<{minimum:.6f}"
        )
    if number > maximum:
        failures.append(
            f"invalid_policy:{label}:above_maximum:"
            f"{number:.6f}>{maximum:.6f}"
        )
    return number


def parse_promotion_policy(payload: Any) -> PolicyParseResult:
    """Parse a complete machine-readable promotion policy fail-closed."""
    if type(payload) is not dict:
        return PolicyParseResult(
            policy=None,
            reasons=("invalid_policy:payload:not_object",),
        )

    required = {
        "minimum_games",
        "minimum_score_rate_lower_bound",
        "minimum_scenario_pass_rate",
        "allow_critical_regressions",
    }
    failures: list[str] = []
    keys = {key for key in payload if type(key) is str}
    if len(keys) != len(payload):
        failures.append("invalid_policy:payload:non_string_key")
    for key in sorted(required - keys):
        failures.append(f"invalid_policy:missing:{key}")
    for key in sorted(keys - required):
        failures.append(f"invalid_policy:unexpected:{key}")

    minimum_games = _policy_integer(
        failures,
        payload.get("minimum_games"),
        "minimum_games",
        minimum=1,
    )
    minimum_score_rate_lower_bound = _policy_number(
        failures,
        payload.get("minimum_score_rate_lower_bound"),
        "minimum_score_rate_lower_bound",
        minimum=0.0,
        maximum=1.0,
    )
    minimum_scenario_pass_rate = _policy_number(
        failures,
        payload.get("minimum_scenario_pass_rate"),
        "minimum_scenario_pass_rate",
        minimum=0.0,
        maximum=1.0,
    )

    allow_critical_regressions = payload.get("allow_critical_regressions")
    if type(allow_critical_regressions) is not bool:
        failures.append(
            "invalid_policy:allow_critical_regressions:not_boolean"
        )
        allow_critical_regressions = False

    if failures:
        return PolicyParseResult(policy=None, reasons=tuple(failures))

    return PolicyParseResult(
        policy=PromotionPolicy(
            minimum_games=minimum_games,
            minimum_score_rate_lower_bound=minimum_score_rate_lower_bound,
            minimum_scenario_pass_rate=minimum_scenario_pass_rate,
            allow_critical_regressions=allow_critical_regressions,
        ),
        reasons=(),
    )


def load_promotion_policy(path: Path) -> PolicyParseResult:
    """Load one regular JSON policy file and parse it fail-closed."""
    if type(path) is not _NATIVE_PATH_TYPE:
        return PolicyParseResult(
            policy=None,
            reasons=("invalid_policy:path:not_path",),
        )
    if path.is_symlink():
        return PolicyParseResult(
            policy=None,
            reasons=("invalid_policy:path:symlink",),
        )
    if not path.is_file():
        return PolicyParseResult(
            policy=None,
            reasons=("invalid_policy:path:not_file",),
        )

    try:
        payload = _load_json(path)
    except _DuplicateJsonKeyError as exc:
        return PolicyParseResult(
            policy=None,
            reasons=(f"invalid_policy:path:duplicate_json_key:{exc.key}",),
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        return PolicyParseResult(
            policy=None,
            reasons=("invalid_policy:path:unreadable_or_invalid_json",),
        )

    return parse_promotion_policy(payload)
