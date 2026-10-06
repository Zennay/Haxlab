from __future__ import annotations

import argparse
import json
import math
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


def _declared_aggregate_integer(
    failures: list[str],
    payload: dict[str, Any],
    key: str,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        failures.append(f"invalid_aggregate:{key}:not_integer")
        return 0
    if value < 0:
        failures.append(f"invalid_aggregate:{key}:negative")
        return 0
    return value


def _declared_aggregate_score(
    failures: list[str],
    payload: dict[str, Any],
) -> float:
    value = payload.get("aggregate_match_score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        failures.append("invalid_aggregate:aggregate_match_score:not_number")
        return 0.0
    number = float(value)
    if not math.isfinite(number):
        failures.append("invalid_aggregate:aggregate_match_score:non_finite")
        return 0.0
    if not 0.0 <= number <= 1.0:
        failures.append("invalid_aggregate:aggregate_match_score:out_of_range")
    return number


def _builder_model_path(
    duel: dict[str, Any],
    key: str,
    source_index: int,
) -> str:
    value = duel.get(key)
    if type(value) is not str or not value.strip():
        raise ValueError(
            f"source {source_index}: {key} must be a non-empty string"
        )
    if value != value.strip():
        raise ValueError(
            f"source {source_index}: {key} must not contain outer whitespace"
        )
    return value


def _builder_count(
    duel: dict[str, Any],
    key: str,
    source_index: int,
) -> int:
    value = duel.get(key)
    if type(value) is not int or value < 0:
        raise ValueError(
            f"source {source_index}: {key} must be a non-negative integer"
        )
    return value


def _builder_scenario_sha(duel: dict[str, Any], source_index: int) -> str:
    value = duel.get("scenario_sha256")
    if type(value) is not str:
        raise ValueError(
            f"source {source_index}: scenario_sha256 must be a string"
        )
    normalized = value.lower()
    if (
        len(normalized) != 64
        or any(ch not in "0123456789abcdef" for ch in normalized)
    ):
        raise ValueError(f"source {source_index}: invalid scenario_sha256")
    return normalized


def build_multisource_duel(duels: list[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(duels, list) or not duels:
        raise ValueError("at least one replay-seeded duel list is required")
    if not isinstance(duels[0], dict):
        raise ValueError("source 1: duel payload must be an object")

    challenger_model = _builder_model_path(
        duels[0], "challenger_model", 1
    )
    champion_model = _builder_model_path(
        duels[0], "champion_model", 1
    )

    scenario_hashes: list[str] = []
    sources: list[dict[str, Any]] = []
    wins = draws = losses = matches = 0

    for index, duel in enumerate(duels, start=1):
        if not isinstance(duel, dict):
            raise ValueError(f"source {index}: duel payload must be an object")
        if duel.get("schema") != "haxlab-elite-replay-seeded-duel-v1":
            raise ValueError(
                f"source {index}: unsupported duel schema {duel.get('schema')!r}"
            )
        if (
            _builder_model_path(duel, "challenger_model", index)
            != challenger_model
        ):
            raise ValueError(f"source {index}: challenger model mismatch")
        if (
            _builder_model_path(duel, "champion_model", index)
            != champion_model
        ):
            raise ValueError(f"source {index}: champion model mismatch")

        scenario_sha = _builder_scenario_sha(duel, index)
        if scenario_sha in scenario_hashes:
            raise ValueError(f"source {index}: duplicate scenario_sha256")
        scenario_hashes.append(scenario_sha)

        source_matches = _builder_count(duel, "matches", index)
        source_wins = _builder_count(duel, "wins", index)
        source_draws = _builder_count(duel, "draws", index)
        source_losses = _builder_count(duel, "losses", index)
        if source_wins + source_draws + source_losses != source_matches:
            raise ValueError(
                f"source {index}: match tally mismatch:"
                f"{source_wins}+{source_draws}+{source_losses}"
                f"!={source_matches}"
            )
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
    if payload.get("evaluation_mode") != "replay_multisource_all_sources_v1":
        failures.append(
            "unsupported_multisource_mode:"
            f"{payload.get('evaluation_mode') or 'missing'}"
        )

    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list):
        failures.append("invalid_sources_payload")
        sources: list[Any] = []
    else:
        sources = raw_sources
    declared_source_count = payload.get("source_count")
    if declared_source_count != len(sources):
        failures.append(
            f"source_count_mismatch:{declared_source_count!r}!={len(sources)}"
        )

    challenger_model = str(payload.get("challenger_model") or "")
    champion_model = str(payload.get("champion_model") or "")
    if not challenger_model:
        failures.append("missing_challenger_model")
    if not champion_model:
        failures.append("missing_champion_model")
    checks["source_count"] = len(sources)
    if len(sources) < policy.minimum_sources:
        failures.append(
            f"insufficient_sources:{len(sources)}<{policy.minimum_sources}"
        )

    scenario_hashes: list[str] = []
    source_results: list[dict[str, Any]] = []
    all_sources_passed = True
    expected_matches = 0
    expected_wins = 0
    expected_draws = 0
    expected_losses = 0

    for index, source in enumerate(sources, start=1):
        if not isinstance(source, dict):
            failures.append(f"source_{index}:invalid_source_payload")
            all_sources_passed = False
            continue

        scenario_sha = str(source.get("scenario_sha256") or "").lower()
        if (
            len(scenario_sha) != 64
            or any(ch not in "0123456789abcdef" for ch in scenario_sha)
        ):
            failures.append(f"source_{index}:invalid_scenario_sha256")
        scenario_hashes.append(scenario_sha)

        declared_index = source.get("source_index")
        if declared_index != index:
            failures.append(
                f"source_{index}:source_index_mismatch:"
                f"{declared_index!r}!={index}"
            )

        duel = source.get("duel") or {}
        duel_scenario_sha = str(duel.get("scenario_sha256") or "").lower()
        if duel_scenario_sha != scenario_sha:
            failures.append(f"source_{index}:scenario_sha256_mismatch")
        if str(duel.get("challenger_model") or "") != challenger_model:
            failures.append(f"source_{index}:challenger_model_mismatch")
        if str(duel.get("champion_model") or "") != champion_model:
            failures.append(f"source_{index}:champion_model_mismatch")

        decision: DuelGateDecision = decide_duel_gate(
            duel,
            policy=duel_policy,
        )
        expected_matches += int(decision.checks.get("matches", 0))
        expected_wins += int(decision.checks.get("wins", 0))
        expected_draws += int(decision.checks.get("draws", 0))
        expected_losses += int(decision.checks.get("losses", 0))
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

    declared_scenario_hashes_raw = payload.get("scenario_sha256s")
    declared_scenario_hashes: list[str] = []
    if not isinstance(declared_scenario_hashes_raw, list):
        failures.append("invalid_scenario_sha256s_payload")
    else:
        for index, value in enumerate(declared_scenario_hashes_raw, start=1):
            if not isinstance(value, str):
                failures.append(
                    f"declared_scenario_{index}:invalid_scenario_sha256"
                )
                declared_scenario_hashes.append("")
                continue
            normalized = value.lower()
            if (
                len(normalized) != 64
                or any(ch not in "0123456789abcdef" for ch in normalized)
            ):
                failures.append(
                    f"declared_scenario_{index}:invalid_scenario_sha256"
                )
            declared_scenario_hashes.append(normalized)
    if declared_scenario_hashes != scenario_hashes:
        failures.append("scenario_sha256s_mismatch")

    checks["source_results"] = source_results
    checks["all_sources_passed"] = all_sources_passed
    checks["scenario_sha256s"] = scenario_hashes
    checks["declared_scenario_sha256s"] = declared_scenario_hashes

    declared_aggregates = {
        "matches": _declared_aggregate_integer(failures, payload, "matches"),
        "wins": _declared_aggregate_integer(failures, payload, "wins"),
        "draws": _declared_aggregate_integer(failures, payload, "draws"),
        "losses": _declared_aggregate_integer(failures, payload, "losses"),
    }
    expected_aggregates = {
        "matches": expected_matches,
        "wins": expected_wins,
        "draws": expected_draws,
        "losses": expected_losses,
    }
    for key, expected in expected_aggregates.items():
        declared = declared_aggregates[key]
        if declared != expected:
            failures.append(
                f"aggregate_{key}_mismatch:{declared}!={expected}"
            )

    declared_match_score = _declared_aggregate_score(failures, payload)
    expected_match_score = (
        (expected_wins + 0.5 * expected_draws) / expected_matches
        if expected_matches > 0
        else 0.0
    )
    if not math.isclose(
        declared_match_score,
        expected_match_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        failures.append(
            "aggregate_match_score_mismatch:"
            f"{declared_match_score:.12f}!={expected_match_score:.12f}"
        )

    checks["aggregate_matches"] = declared_aggregates["matches"]
    checks["aggregate_wins"] = declared_aggregates["wins"]
    checks["aggregate_draws"] = declared_aggregates["draws"]
    checks["aggregate_losses"] = declared_aggregates["losses"]
    checks["aggregate_match_score"] = declared_match_score
    checks["expected_aggregate_matches"] = expected_matches
    checks["expected_aggregate_wins"] = expected_wins
    checks["expected_aggregate_draws"] = expected_draws
    checks["expected_aggregate_losses"] = expected_losses
    checks["expected_aggregate_match_score"] = expected_match_score

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
