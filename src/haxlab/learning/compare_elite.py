from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


METRICS = (
    "direction_accuracy",
    "macro_direction_recall",
    "joint_accuracy",
    "kick_f1",
    "future_direction_accuracy",
    "future_direction_lift",
)


def _role_rows(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    final = metrics.get("final_holdout") or {}
    return dict(final.get("by_role") or {})


def compare_metrics(
    baseline: dict[str, Any],
    challenger: dict[str, Any],
) -> dict[str, Any]:
    baseline_holdout = baseline.get("final_holdout") or {}
    challenger_holdout = challenger.get("final_holdout") or {}

    overall = {}
    for metric in METRICS:
        before = baseline_holdout.get(metric)
        after = challenger_holdout.get(metric)
        if before is None or after is None:
            continue
        overall[metric] = {
            "baseline": float(before),
            "challenger": float(after),
            "delta": float(after) - float(before),
        }

    roles = {}
    baseline_roles = _role_rows(baseline)
    challenger_roles = _role_rows(challenger)
    for role in sorted(set(baseline_roles) | set(challenger_roles)):
        role_delta = {}
        for metric in METRICS:
            before = (baseline_roles.get(role) or {}).get(metric)
            after = (challenger_roles.get(role) or {}).get(metric)
            if before is None or after is None:
                continue
            role_delta[metric] = {
                "baseline": float(before),
                "challenger": float(after),
                "delta": float(after) - float(before),
            }
        roles[role] = role_delta

    return {
        "schema": "haxlab-elite-metrics-comparison-v1",
        "overall_holdout": overall,
        "by_role_holdout": roles,
        "baseline_training": {
            "best_epoch": (baseline.get("training") or {}).get("best_epoch"),
            "state_jitter": (baseline.get("training") or {}).get("state_jitter"),
        },
        "challenger_training": {
            "best_epoch": (challenger.get("training") or {}).get("best_epoch"),
            "state_jitter": (challenger.get("training") or {}).get("state_jitter"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-compare-elite")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("challenger", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = compare_metrics(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.challenger.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
