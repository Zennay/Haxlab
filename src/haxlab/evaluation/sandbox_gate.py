from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SandboxGatePolicy:
    minimum_matches: int = 6
    maximum_runtime_errors: int = 0
    minimum_non_loss_rate: float = 0.50
    minimum_territory_share: float = 0.35
    minimum_attack_third_share: float = 0.25
    maximum_side_territory_gap: float = 0.25
    maximum_kick_action_rate: float = 0.10
    required_profiles: tuple[str, ...] = ("balanced", "compact", "press")


@dataclass(frozen=True)
class SandboxGateDecision:
    eligible_for_champion_promotion: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def _share(for_rate: float, against_rate: float) -> float:
    total = for_rate + against_rate
    return for_rate / total if total > 0 else 0.5


def decide_sandbox_gate(
    benchmark: dict[str, Any],
    policy: SandboxGatePolicy = SandboxGatePolicy(),
) -> SandboxGateDecision:
    failures: list[str] = []
    checks: dict[str, Any] = {}

    schema = str(benchmark.get("schema") or "")
    if schema != "haxlab-elite-sandbox-benchmark-v1":
        failures.append(f"unsupported_benchmark_schema:{schema or 'missing'}")

    matches = int(benchmark.get("matches") or 0)
    checks["matches"] = matches
    if matches < policy.minimum_matches:
        failures.append(
            f"insufficient_matches:{matches}<{policy.minimum_matches}"
        )

    runtime_errors = int(benchmark.get("runtime_errors") or 0)
    checks["runtime_errors"] = runtime_errors
    if runtime_errors > policy.maximum_runtime_errors:
        failures.append(
            f"runtime_errors:{runtime_errors}>{policy.maximum_runtime_errors}"
        )

    non_loss_rate = float(benchmark.get("non_loss_rate") or 0.0)
    checks["non_loss_rate"] = non_loss_rate
    if non_loss_rate < policy.minimum_non_loss_rate:
        failures.append(
            f"non_loss_rate:{non_loss_rate:.4f}<{policy.minimum_non_loss_rate:.4f}"
        )

    territory = benchmark.get("territory") or {}
    elite_half = float(territory.get("elite_half_rate") or 0.0)
    baseline_half = float(territory.get("baseline_half_rate") or 0.0)
    territory_share = _share(elite_half, baseline_half)
    checks["territory_share"] = territory_share
    if territory_share < policy.minimum_territory_share:
        failures.append(
            "territory_share:"
            f"{territory_share:.4f}<{policy.minimum_territory_share:.4f}"
        )

    elite_attack = float(territory.get("elite_attack_third_rate") or 0.0)
    baseline_attack = float(territory.get("baseline_attack_third_rate") or 0.0)
    attack_share = _share(elite_attack, baseline_attack)
    checks["attack_third_share"] = attack_share
    if attack_share < policy.minimum_attack_third_share:
        failures.append(
            "attack_third_share:"
            f"{attack_share:.4f}<{policy.minimum_attack_third_share:.4f}"
        )

    sides = benchmark.get("by_elite_side") or {}
    side_rates: dict[str, float] = {}
    for side in ("1", "2"):
        row = sides.get(side)
        if not row:
            failures.append(f"missing_side_metrics:{side}")
            continue
        side_rates[side] = float(row.get("elite_half_rate") or 0.0)
    checks["side_elite_half_rates"] = side_rates

    if len(side_rates) == 2:
        side_gap = abs(side_rates["1"] - side_rates["2"])
        checks["side_territory_gap"] = side_gap
        if side_gap > policy.maximum_side_territory_gap:
            failures.append(
                "side_territory_gap:"
                f"{side_gap:.4f}>{policy.maximum_side_territory_gap:.4f}"
            )

    profiles = set((benchmark.get("by_profile") or {}).keys())
    missing_profiles = [
        profile for profile in policy.required_profiles if profile not in profiles
    ]
    checks["profiles_seen"] = sorted(profiles)
    if missing_profiles:
        failures.append(
            "missing_profiles:" + ",".join(missing_profiles)
        )

    match_results = list(benchmark.get("match_results") or [])
    total_actions = sum(
        int(((row.get("policy") or {}).get("total_actions")) or 0)
        for row in match_results
    )
    total_kicks = sum(
        int(((row.get("policy") or {}).get("total_kicks")) or 0)
        for row in match_results
    )
    kick_action_rate = total_kicks / total_actions if total_actions > 0 else 0.0
    checks["kick_action_rate"] = kick_action_rate
    checks["policy_actions"] = total_actions
    checks["policy_kicks"] = total_kicks
    if total_actions <= 0:
        failures.append("no_policy_actions")
    elif kick_action_rate > policy.maximum_kick_action_rate:
        failures.append(
            "kick_action_rate:"
            f"{kick_action_rate:.4f}>{policy.maximum_kick_action_rate:.4f}"
        )

    if failures:
        return SandboxGateDecision(
            eligible_for_champion_promotion=False,
            reasons=tuple(failures),
            checks=checks,
        )

    return SandboxGateDecision(
        eligible_for_champion_promotion=True,
        reasons=(
            "sandbox_runtime_stable",
            "sandbox_territory_passed",
            "red_blue_symmetry_passed",
            "kick_action_rate_passed",
            "opponent_profile_coverage_passed",
        ),
        checks=checks,
    )


def evaluate_benchmark_file(
    benchmark_path: Path,
    policy: SandboxGatePolicy = SandboxGatePolicy(),
) -> dict[str, Any]:
    benchmark = json.loads(benchmark_path.read_text(encoding="utf-8"))
    decision = decide_sandbox_gate(benchmark, policy)
    return {
        "schema": "haxlab-elite-sandbox-gate-v1",
        "benchmark_path": str(benchmark_path),
        "policy": asdict(policy),
        "eligible_for_champion_promotion": (
            decision.eligible_for_champion_promotion
        ),
        "reasons": list(decision.reasons),
        "checks": decision.checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-sandbox-gate")
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = evaluate_benchmark_file(args.benchmark)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["eligible_for_champion_promotion"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
