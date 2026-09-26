from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from haxlab.evaluation.duel_gate import (
    DuelGateDecision,
    DuelGatePolicy,
    decide_duel_gate,
)


MULTISOURCE_DUEL_SCHEMA = "haxlab-elite-replay-multisource-duel-v1"


@dataclass(frozen=True)
class MultisourceDuelPolicy:
    minimum_sources: int = 3


@dataclass(frozen=True)
class MultisourceDuelDecision:
    eligible_to_replace_champion: bool
    reasons: tuple[str, ...]
    checks: dict[str, Any]


def build_multisource_duel(duels: list[dict[str, Any]]) -> dict[str, Any]:
    if not duels:
        raise ValueError("at least one replay-seeded duel is required")

    challenger_model = str(duels[0].get("challenger_model") or "")
    champion_model = str(duels[0].get("champion_model") or "")
    if not challenger_model or not champion_model:
        raise ValueError("duel model paths are required")

    scenario_hashes: list[str] = []
    sources: list[dict[str, Any]] = []
    wins = draws = losses = matches = 0

    for index, duel in enumerate(duels, start=1):
        if duel.get("schema") != "haxlab-elite-replay-seeded-duel-v1":
            raise ValueError(
                f"source {index}: unsupported duel schema {duel.get('schema')!r}"
            )
        if str(duel.get("challenger_model") or "") != challenger_model:
            raise ValueError(f"source {index}: challenger model mismatch")
        if str(duel.get("champion_model") or "") != champion_model:
            raise ValueError(f"source {index}: champion model mismatch")

        scenario_sha = str(duel.get("scenario_sha256") or "")
        if len(scenario_sha) != 64:
            raise ValueError(f"source {index}: invalid scenario_sha256")
        if scenario_sha in scenario_hashes:
            raise ValueError(f"source {index}: duplicate scenario_sha256")
        scenario_hashes.append(scenario_sha)

        source_matches = int(duel.get("matches") or 0)
        source_wins = int(duel.get("wins") or 0)
        source_draws = int(duel.get("draws") or 0)
        source_losses = int(duel.get("losses") or 0)
        matches += source_matches
        wins += source_wins
        draws += source_draws
        losses += source_losses

        sources.append(
            {
                "source_index": index,
                "scenario_sha256": scenario_sha,
                "duel": duel,
            }
        )

    return {
        "schema": MULTISOURCE_DUEL_SCHEMA,
        "evaluation_mode": "replay_multisource_all_sources_v1",
        "challenger_model": challenger_model,
        "champion_model": champion_model,
        "source_count": len(sources),
        "scenario_sha256s": scenario_hashes,
        "matches": matches,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "aggregate_match_score": (
            (wins + 0.5 * draws) / matches if matches > 0 else 0.0
        ),
        "sources": sources,
    }


def decide_multisource_duel_gate(
    payload: dict[str, Any],
    *,
    policy: MultisourceDuelPolicy = MultisourceDuelPolicy(),
    duel_policy: DuelGatePolicy = DuelGatePolicy(),
) -> MultisourceDuelDecision:
    failures: list[str] = []
    checks: dict[str, Any] = {
        "evaluation_schema": payload.get("schema"),
        "evaluation_mode": payload.get("evaluation_mode"),
    }
    if payload.get("schema") != MULTISOURCE_DUEL_SCHEMA:
        failures.append(
            f"unsupported_multisource_schema:{payload.get('schema') or 'missing'}"
        )

    sources = list(payload.get("sources") or [])
    checks["source_count"] = len(sources)
    if len(sources) < policy.minimum_sources:
        failures.append(
            f"insufficient_sources:{len(sources)}<{policy.minimum_sources}"
        )

    scenario_hashes: list[str] = []
    source_results: list[dict[str, Any]] = []
    all_sources_passed = True

    for index, source in enumerate(sources, start=1):
        scenario_sha = str(source.get("scenario_sha256") or "")
        scenario_hashes.append(scenario_sha)
        duel = source.get("duel") or {}
        decision: DuelGateDecision = decide_duel_gate(
            duel,
            policy=duel_policy,
        )
        source_results.append(
            {
                "source_index": index,
                "scenario_sha256": scenario_sha,
                "eligible_to_replace_champion": (
                    decision.eligible_to_replace_champion
                ),
                "reasons": list(decision.reasons),
                "checks": decision.checks,
            }
        )
        if not decision.eligible_to_replace_champion:
            all_sources_passed = False
            failures.extend(
                f"source_{index}:{reason}" for reason in decision.reasons
            )

    if len(set(scenario_hashes)) != len(scenario_hashes):
        failures.append("duplicate_scenario_sources")

    checks["source_results"] = source_results
    checks["all_sources_passed"] = all_sources_passed
    checks["aggregate_matches"] = int(payload.get("matches") or 0)
    checks["aggregate_match_score"] = float(
        payload.get("aggregate_match_score") or 0.0
    )

    if failures:
        return MultisourceDuelDecision(
            eligible_to_replace_champion=False,
            reasons=tuple(failures),
            checks=checks,
        )

    return MultisourceDuelDecision(
        eligible_to_replace_champion=True,
        reasons=(
            "multisource_count_passed",
            "all_replay_sources_passed_existing_duel_gate",
        ),
        checks=checks,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-multisource-duel")
    parser.add_argument("duels", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    payloads = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in args.duels
    ]
    result = build_multisource_duel(payloads)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
