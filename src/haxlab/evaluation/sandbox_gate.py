from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SandboxGatePolicy:
    maximum_model_free_side_bias: float = 0.18
    maximum_runtime_errors: int = 0
    minimum_progression_share: float = 0.42
    minimum_non_loss_rate: float = 0.50
    minimum_nonzero_movement_rate: float = 0.10
    minimum_total_kicks: int = 1


def evaluate_sandbox_evidence(
    benchmark: dict[str, Any],
    side_sanity: dict[str, Any],
    policy: SandboxGatePolicy = SandboxGatePolicy(),
) -> dict[str, Any]:
    reasons: list[str] = []

    side_bias = float(
        side_sanity.get("aggregate_side_bias_abs")
        if side_sanity.get("aggregate_side_bias_abs") is not None
        else side_sanity.get("average_side_bias_abs") or 0.0
    )
    runtime_errors = int(benchmark.get("runtime_errors") or 0)
    progression_share = float(
        (benchmark.get("progression") or {}).get("elite_share") or 0.0
    )
    non_loss_rate = float(benchmark.get("non_loss_rate") or 0.0)
    activity = benchmark.get("policy_activity") or {}
    nonzero_movement_rate = float(
        activity.get("nonzero_movement_rate") or 0.0
    )
    total_kicks = int(activity.get("total_kicks") or 0)

    arena_valid = side_bias <= policy.maximum_model_free_side_bias
    if not arena_valid:
        reasons.append(
            f"arena_side_bias:{side_bias:.4f}>"
            f"{policy.maximum_model_free_side_bias:.4f}"
        )

    if runtime_errors > policy.maximum_runtime_errors:
        reasons.append(
            f"runtime_errors:{runtime_errors}>"
            f"{policy.maximum_runtime_errors}"
        )

    if progression_share < policy.minimum_progression_share:
        reasons.append(
            f"progression_share:{progression_share:.4f}<"
            f"{policy.minimum_progression_share:.4f}"
        )

    if non_loss_rate < policy.minimum_non_loss_rate:
        reasons.append(
            f"non_loss_rate:{non_loss_rate:.4f}<"
            f"{policy.minimum_non_loss_rate:.4f}"
        )

    if nonzero_movement_rate < policy.minimum_nonzero_movement_rate:
        reasons.append(
            f"nonzero_movement_rate:{nonzero_movement_rate:.4f}<"
            f"{policy.minimum_nonzero_movement_rate:.4f}"
        )

    if total_kicks < policy.minimum_total_kicks:
        reasons.append(
            f"total_kicks:{total_kicks}<"
            f"{policy.minimum_total_kicks}"
        )

    eligible = arena_valid and not reasons
    return {
        "schema": "haxlab-sandbox-promotion-gate-v1",
        "eligible_for_champion_match": eligible,
        "arena_valid": arena_valid,
        "reasons": reasons or [
            "arena_side_sanity_passed",
            "runtime_stability_passed",
            "progression_passed",
            "non_loss_rate_passed",
        ],
        "checks": {
            "model_free_side_bias": side_bias,
            "runtime_errors": runtime_errors,
            "progression_share": progression_share,
            "non_loss_rate": non_loss_rate,
            "nonzero_movement_rate": nonzero_movement_rate,
            "total_kicks": total_kicks,
        },
        "policy": asdict(policy),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-sandbox-gate")
    parser.add_argument("--benchmark", type=Path, required=True)
    parser.add_argument("--side-sanity", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    side_sanity = json.loads(args.side_sanity.read_text(encoding="utf-8"))
    result = evaluate_sandbox_evidence(benchmark, side_sanity)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["arena_valid"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
