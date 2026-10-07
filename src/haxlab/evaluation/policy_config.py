from __future__ import annotations

import argparse
import hashlib
import json
import math
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from haxlab.evaluation.models import PromotionPolicy


POLICY_CONFIG_SCHEMA = "haxlab-promotion-policy-config-v1"

_EVALUATION_KEYS = {
    "minimum_games_vs_champion",
    "minimum_score_rate_lower_bound",
    "minimum_frozen_scenario_pass_rate",
}


@dataclass(frozen=True)
class LoadedPromotionPolicy:
    schema: str
    policy: PromotionPolicy
    source_sha256: str
    source_size_bytes: int


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


def load_promotion_policy_config(path: Path) -> LoadedPromotionPolicy:
    """Load a strict promotion policy plus immutable source provenance."""

    if not isinstance(path, Path):
        raise ValueError("promotion policy path must be pathlib.Path")
    if path.is_symlink() or not path.is_file():
        raise ValueError("promotion policy path must be a regular non-symlink file")

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError("promotion policy config is unreadable") from exc

    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
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

    policy = PromotionPolicy(
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
    return LoadedPromotionPolicy(
        schema=POLICY_CONFIG_SCHEMA,
        policy=policy,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        source_size_bytes=len(raw),
    )


def load_promotion_policy(path: Path) -> PromotionPolicy:
    """Load only the validated runtime policy from the strict config boundary."""

    return load_promotion_policy_config(path).policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.evaluation.policy_config",
        description="Validate and render the strict HaxLab promotion policy config.",
    )
    parser.add_argument("config", type=Path)
    args = parser.parse_args(argv)

    try:
        loaded = load_promotion_policy_config(args.config)
    except ValueError as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "schema": loaded.schema,
                "policy": asdict(loaded.policy),
                "source": {
                    "sha256": loaded.source_sha256,
                    "size_bytes": loaded.source_size_bytes,
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
