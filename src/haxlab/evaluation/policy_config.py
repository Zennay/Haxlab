from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, get_type_hints

from haxlab.evaluation.models import PromotionPolicy


POLICY_CONFIG_SCHEMA = "haxlab-promotion-policy-config-v1"
MAX_POLICY_CONFIG_BYTES = 64 * 1024

_EVALUATION_KEYS = {
    "minimum_games_vs_champion",
    "minimum_score_rate_lower_bound",
    "minimum_frozen_scenario_pass_rate",
}
_POLICY_FIELDS = {
    "minimum_games",
    "minimum_score_rate_lower_bound",
    "minimum_scenario_pass_rate",
    "allow_critical_regressions",
}
_POLICY_FIELD_TYPES = {
    "minimum_games": int,
    "minimum_score_rate_lower_bound": float,
    "minimum_scenario_pass_rate": float,
    "allow_critical_regressions": bool,
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


def _require_runtime_policy_schema() -> None:
    try:
        actual_fields = {field.name for field in fields(PromotionPolicy)}
    except TypeError as exc:
        raise ValueError("promotion policy runtime model must be a dataclass") from exc
    if actual_fields != _POLICY_FIELDS:
        missing = sorted(_POLICY_FIELDS - actual_fields)
        extra = sorted(actual_fields - _POLICY_FIELDS)
        raise ValueError(
            "promotion policy runtime schema mismatch: "
            f"missing={missing}, extra={extra}"
        )

    dataclass_params = getattr(PromotionPolicy, "__dataclass_params__", None)
    if dataclass_params is None or not dataclass_params.frozen:
        raise ValueError("promotion policy runtime model must be a frozen dataclass")

    try:
        type_hints = get_type_hints(PromotionPolicy)
    except (NameError, TypeError) as exc:
        raise ValueError("promotion policy runtime annotations are unreadable") from exc
    actual_types = {name: type_hints.get(name) for name in actual_fields}
    if actual_types != _POLICY_FIELD_TYPES:
        drifted = sorted(
            name
            for name in _POLICY_FIELDS
            if actual_types.get(name) != _POLICY_FIELD_TYPES[name]
        )
        raise ValueError(
            "promotion policy runtime type schema mismatch: "
            f"drifted={drifted}"
        )



def _read_regular_file_nofollow(path: Path) -> bytes:
    """Read one regular file without a symlink check/read race."""

    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        raise ValueError("secure promotion policy file-open primitives are unsupported")

    try:
        fd = os.open(path, os.O_RDONLY | nofollow | nonblock)
    except OSError as exc:
        raise ValueError(
            "promotion policy path must be a readable regular non-symlink file"
        ) from exc

    try:
        try:
            metadata = os.fstat(fd)
        except OSError as exc:
            raise ValueError("promotion policy config metadata is unreadable") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                "promotion policy path must be a readable regular non-symlink file"
            )
        if metadata.st_size > MAX_POLICY_CONFIG_BYTES:
            raise ValueError(
                f"promotion policy config exceeds {MAX_POLICY_CONFIG_BYTES} bytes"
            )
        try:
            with os.fdopen(fd, "rb", closefd=False) as handle:
                raw = handle.read(MAX_POLICY_CONFIG_BYTES + 1)
        except OSError as exc:
            raise ValueError("promotion policy config is unreadable") from exc
        if len(raw) > MAX_POLICY_CONFIG_BYTES:
            raise ValueError(
                f"promotion policy config exceeds {MAX_POLICY_CONFIG_BYTES} bytes"
            )
        return raw
        except OSError as exc:
            raise ValueError("promotion policy config is unreadable") from exc
    finally:
        os.close(fd)


def load_promotion_policy_config(path: Path) -> LoadedPromotionPolicy:
    """Load a strict promotion policy plus immutable source provenance."""

    if not isinstance(path, Path):
        raise ValueError("promotion policy path must be pathlib.Path")

    raw = _read_regular_file_nofollow(path)

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

    _require_runtime_policy_schema()

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
