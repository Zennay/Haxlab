from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DuelGatePolicy:
    minimum_matches: int = 12
    minimum_match_score: float = 0.55
    minimum_territory_share: float = 0.50
    minimum_attack_third_share: float = 0.45
    maximum_side_territory_gap: float = 0.20
    maximum_runtime_errors: int = 0
    maximum_kick_action_rate: float = 0.10


@dataclass(frozen=True)
class DuelGateDecision:
    eligible_to_replace_champion: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def _share(for_rate: float, against_rate: float) -> float:
    total = for_rate + against_rate
    return for_rate / total if total > 0 else 0.5


def decide_duel_gate(
    duel: dict[str, Any],
    policy: DuelGatePolicy = DuelGatePolicy(),
) -> DuelGateDecision:
    failures: list[str] = []
    checks: dict[str, Any] = {}

    schema = str(duel.get("schema") or "")
    if schema != "haxlab-elite-model-duel-v1":
        failures.append(f"unsupported_duel_schema:{schema or 'missing'}")

    matches = int(duel.get("matches") or 0)
    checks["matches"] = matches
    if matches < policy.minimum_matches:
        failures.append(
            f"insufficient_matches:{matches}<{policy.minimum_matches}"
        )

    wins = int(duel.get("wins") or 0)
    draws = int(duel.get("draws") or 0)
    match_score = (wins + 0.5 * draws) / matches if matches > 0 else 0.0
    checks["match_score"] = match_score
    if match_score < policy.minimum_match_score:
        failures.append(
            f"match_score:{match_score:.4f}<{policy.minimum_match_score:.4f}"
        )

    territory = duel.get("territory") or {}
    challenger_half = float(territory.get("challenger_half_rate") or 0.0)
    champion_half = float(territory.get("champion_half_rate") or 0.0)
    territory_share = _share(challenger_half, champion_half)
    checks["territory_share"] = territory_share
    if territory_share < policy.minimum_territory_share:
        failures.append(
            "territory_share:"
            f"{territory_share:.4f}<{policy.minimum_territory_share:.4f}"
        )

    challenger_attack = float(
        territory.get("challenger_attack_third_rate") or 0.0
    )
    champion_attack = float(
        territory.get("champion_attack_third_rate") or 0.0
    )
    attack_share = _share(challenger_attack, champion_attack)
    checks["attack_third_share"] = attack_share
    if attack_share < policy.minimum_attack_third_share:
        failures.append(
            "attack_third_share:"
            f"{attack_share:.4f}<{policy.minimum_attack_third_share:.4f}"
        )

    challenger_errors = int(duel.get("challenger_runtime_errors") or 0)
    champion_errors = int(duel.get("champion_runtime_errors") or 0)
    checks["challenger_runtime_errors"] = challenger_errors
    checks["champion_runtime_errors"] = champion_errors
    if challenger_errors > policy.maximum_runtime_errors:
        failures.append(
            "challenger_runtime_errors:"
            f"{challenger_errors}>{policy.maximum_runtime_errors}"
        )
    if champion_errors > policy.maximum_runtime_errors:
        failures.append(
            "champion_runtime_errors:"
            f"{champion_errors}>{policy.maximum_runtime_errors}"
        )

    kick_rate = float(duel.get("challenger_kick_action_rate") or 0.0)
    checks["challenger_kick_action_rate"] = kick_rate
    if kick_rate > policy.maximum_kick_action_rate:
        failures.append(
            "challenger_kick_action_rate:"
            f"{kick_rate:.4f}>{policy.maximum_kick_action_rate:.4f}"
        )

    sides = duel.get("by_challenger_side") or {}
    side_rates: dict[str, float] = {}
    for side in ("1", "2"):
        row = sides.get(side)
        if not row:
            failures.append(f"missing_side_metrics:{side}")
            continue
        side_rates[side] = float(row.get("challenger_half_rate") or 0.0)
    checks["side_challenger_half_rates"] = side_rates
    if len(side_rates) == 2:
        side_gap = abs(side_rates["1"] - side_rates["2"])
        checks["side_territory_gap"] = side_gap
        if side_gap > policy.maximum_side_territory_gap:
            failures.append(
                "side_territory_gap:"
                f"{side_gap:.4f}>{policy.maximum_side_territory_gap:.4f}"
            )

    goal_diff = int((duel.get("goals") or {}).get("differential") or 0)
    checks["goal_differential"] = goal_diff

    if failures:
        return DuelGateDecision(
            eligible_to_replace_champion=False,
            reasons=tuple(failures),
            checks=checks,
        )

    return DuelGateDecision(
        eligible_to_replace_champion=True,
        reasons=(
            "duel_match_score_passed",
            "duel_territory_passed",
            "duel_side_symmetry_passed",
            "duel_runtime_stable",
            "duel_kick_rate_passed",
        ),
        checks=checks,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-duel-gate")
    parser.add_argument("duel", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    duel = json.loads(args.duel.read_text(encoding="utf-8"))
    decision = decide_duel_gate(duel)
    result = {
        "schema": "haxlab-elite-duel-gate-v1",
        "duel_path": str(args.duel),
        "policy": asdict(DuelGatePolicy()),
        "eligible_to_replace_champion": decision.eligible_to_replace_champion,
        "reasons": list(decision.reasons),
        "checks": decision.checks,
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["eligible_to_replace_champion"] else 2


if __name__ == "__main__":
    raise SystemExit(main())