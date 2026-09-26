from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from haxlab.evaluation.elite_gate import decide_elite_live_gate
from haxlab.evaluation.sandbox_gate import decide_sandbox_gate


def decide_champion_promotion(
    metrics: dict[str, Any],
    benchmark: dict[str, Any],
) -> dict[str, Any]:
    offline = decide_elite_live_gate(metrics)
    sandbox = decide_sandbox_gate(benchmark)

    eligible = (
        offline.eligible_for_live_test
        and sandbox.eligible_for_champion_promotion
    )
    reasons: list[str] = []
    if offline.eligible_for_live_test:
        reasons.append("offline_gate_passed")
    else:
        reasons.extend(f"offline:{reason}" for reason in offline.reasons)

    if sandbox.eligible_for_champion_promotion:
        reasons.append("sandbox_gate_passed")
    else:
        reasons.extend(f"sandbox:{reason}" for reason in sandbox.reasons)

    if eligible:
        reasons.append("eligible_for_champion_promotion")

    return {
        "schema": "haxlab-elite-champion-gate-v1",
        "eligible_for_champion_promotion": eligible,
        "reasons": reasons,
        "offline": {
            "eligible_for_live_test": offline.eligible_for_live_test,
            "reasons": list(offline.reasons),
            "checks": offline.checks,
        },
        "sandbox": {
            "eligible_for_champion_promotion": (
                sandbox.eligible_for_champion_promotion
            ),
            "reasons": list(sandbox.reasons),
            "checks": sandbox.checks,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-champion-gate")
    parser.add_argument("metrics", type=Path)
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    metrics = json.loads(args.metrics.read_text(encoding="utf-8"))
    benchmark = json.loads(args.benchmark.read_text(encoding="utf-8"))
    result = decide_champion_promotion(metrics, benchmark)
    result["metrics_path"] = str(args.metrics)
    result["benchmark_path"] = str(args.benchmark)

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["eligible_for_champion_promotion"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
