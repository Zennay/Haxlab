from __future__ import annotations

import math
import tomllib
from pathlib import Path
from typing import Any

from haxlab.evaluation.models import PromotionPolicy


_EVALUATION_KEYS = {
    "minimum_games_vs_champion",
    "minimum_score_rate_lower_bound",
    "minimum_frozen_scenario_pass_rate",
}


def _require_native_int(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be a native integer")
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def _require_probability(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a native numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} must be within [0, 1]")
    return number


def load_promotion_policy(path: Path) -> PromotionPolicy:
    """Load the promotion gate from the canonical autonomy TOML contract.

    The loader is deliberately strict and fail-closed. It accepts only a regular,
    non-symlink file, requires the exact evaluation keyset, and rejects coercible
    booleans/strings/non-finite thresholds before constructing PromotionPolicy.
    """

    if not isinstance(path, Path):
        raise ValueError("promotion policy path must be pathlib.Path")
    if path.is_symlink() or not path.is_file():
        raise ValueError("promotion policy path must be a regular non-symlink file")

    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("promotion policy config is unreadable or invalid TOML") from exc

    if not isinstance(document, dict):
        raise ValueError("promotion policy config root must be a table")

    evaluation = document.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("promotion policy config requires an [evaluation] table")

    actual_keys = set(evaluation)
    if actual_keys != _EVALUATION_KEYS:
        missing = sorted(_EVALUATION_KEYS - actual_keys)
        extra = sorted(actual_keys - _EVALUATION_KEYS)
        raise ValueError(
            "promotion policy evaluation keyset mismatch: "
            f"missing={missing}, extra={extra}"
        )

    return PromotionPolicy(
        minimum_games=_require_native_int(
            evaluation["minimum_games_vs_champion"],
            "evaluation.minimum_games_vs_champion",
            minimum=1,
        ),
        minimum_score_rate_lower_bound=_require_probability(
            evaluation["minimum_score_rate_lower_bound"],
            "evaluation.minimum_score_rate_lower_bound",
        ),
        minimum_scenario_pass_rate=_require_probability(
            evaluation["minimum_frozen_scenario_pass_rate"],
            "evaluation.minimum_frozen_scenario_pass_rate",
        ),
        allow_critical_regressions=False,
    )
