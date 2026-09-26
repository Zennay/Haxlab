from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ARENA_SCHEMA = "haxlab-closed-loop-arena-v2"
COMPARISON_SCHEMA = "haxlab-closed-loop-arena-comparison-v2"


def _require(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"arena artifact missing {key}")
    return value


def validate_arena_artifact(payload: dict[str, Any]) -> None:
    if payload.get("schema") != ARENA_SCHEMA:
        raise ValueError(
            f"unsupported arena schema: {payload.get('schema')!r}"
        )
    provenance = _require(payload, "provenance")
    aggregate = _require(payload, "aggregate")
    for key in (
        "code_ref",
        "model_sha256",
        "suite_manifest_sha256",
        "config_sha256",
        "source_replay_sha256s",
        "stadium_sha256s",
        "scenario_sha256s",
    ):
        if provenance.get(key) in (None, "", []):
            raise ValueError(f"arena provenance missing {key}")
    if len(provenance["source_replay_sha256s"]) < 3:
        raise ValueError("arena artifact requires at least three sources")
    for mode in ("raw", "guarded"):
        summary = aggregate.get(mode) or {}
        if int(summary.get("episodes") or 0) <= 0:
            raise ValueError(f"arena {mode} summary has no episodes")
        if int(summary.get("runtime_errors") or 0) < 0:
            raise ValueError(f"arena {mode} runtime error count is invalid")
    if aggregate["raw"]["episodes"] != aggregate["guarded"]["episodes"]:
        raise ValueError("raw/guarded rollout coverage mismatch")


def _delta(challenger: float, baseline: float) -> dict[str, float]:
    return {
        "baseline": float(baseline),
        "challenger": float(challenger),
        "delta": float(challenger) - float(baseline),
    }


def _summary_delta(
    baseline: dict[str, Any],
    challenger: dict[str, Any],
) -> dict[str, Any]:
    return {
        "match_score": _delta(
            challenger["match_score"],
            baseline["match_score"],
        ),
        "goal_differential": _delta(
            challenger["goal_differential"],
            baseline["goal_differential"],
        ),
        "progression_share": _delta(
            challenger["progression_share"],
            baseline["progression_share"],
        ),
        "territory_elite_half_rate": _delta(
            challenger["territory"]["elite_half_rate"],
            baseline["territory"]["elite_half_rate"],
        ),
        "territory_attack_third_rate": _delta(
            challenger["territory"]["elite_attack_third_rate"],
            baseline["territory"]["elite_attack_third_rate"],
        ),
        "formation_order_rate": _delta(
            challenger["team_shape"]["formation_order_rate"],
            baseline["team_shape"]["formation_order_rate"],
        ),
        "shape_span_instability": _delta(
            challenger["team_shape"]["mean_std_x_span"],
            baseline["team_shape"]["mean_std_x_span"],
        ),
        "pairwise_instability": _delta(
            challenger["team_shape"]["mean_std_pairwise_distance"],
            baseline["team_shape"]["mean_std_pairwise_distance"],
        ),
        "guard_intervention_rate": _delta(
            challenger["guard_intervention_rate"],
            baseline["guard_intervention_rate"],
        ),
    }


def compare_arena_artifacts(
    baseline: dict[str, Any],
    challenger: dict[str, Any],
) -> dict[str, Any]:
    validate_arena_artifact(baseline)
    validate_arena_artifact(challenger)

    bp = baseline["provenance"]
    cp = challenger["provenance"]
    for key in (
        "code_ref",
        "suite_manifest_sha256",
        "config_sha256",
        "source_replay_sha256s",
        "stadium_sha256s",
        "scenario_sha256s",
    ):
        if bp[key] != cp[key]:
            raise ValueError(
                f"arena comparison provenance mismatch for {key}"
            )
    if bp["model_sha256"] == cp["model_sha256"]:
        raise ValueError("arena comparison requires distinct model SHA-256s")

    baseline_sources = {
        row["source_id"]: row for row in baseline.get("sources") or []
    }
    challenger_sources = {
        row["source_id"]: row for row in challenger.get("sources") or []
    }
    if set(baseline_sources) != set(challenger_sources):
        raise ValueError("arena comparison source coverage mismatch")

    source_results = []
    for source_id in sorted(baseline_sources):
        b = baseline_sources[source_id]
        c = challenger_sources[source_id]
        for key in (
            "replay_sha256",
            "source_json_sha256",
            "stadium_sha256",
            "scenario_sha256",
            "scenario_count",
        ):
            if b.get(key) != c.get(key):
                raise ValueError(
                    f"arena source provenance mismatch: {source_id}/{key}"
                )
        source_results.append(
            {
                "source_id": source_id,
                "replay_sha256": b["replay_sha256"],
                "raw": _summary_delta(b["raw"], c["raw"]),
                "guarded": _summary_delta(
                    b["guarded"],
                    c["guarded"],
                ),
                "guard_dependency": {
                    "baseline": b["guard_dependency"],
                    "challenger": c["guard_dependency"],
                },
            }
        )

    return {
        "schema": COMPARISON_SCHEMA,
        "provenance": {
            "code_ref": bp["code_ref"],
            "suite_manifest_sha256": bp["suite_manifest_sha256"],
            "config_sha256": bp["config_sha256"],
            "baseline_model_sha256": bp["model_sha256"],
            "challenger_model_sha256": cp["model_sha256"],
            "source_replay_sha256s": bp["source_replay_sha256s"],
            "stadium_sha256s": bp["stadium_sha256s"],
            "scenario_sha256s": bp["scenario_sha256s"],
        },
        "aggregate": {
            "raw": _summary_delta(
                baseline["aggregate"]["raw"],
                challenger["aggregate"]["raw"],
            ),
            "guarded": _summary_delta(
                baseline["aggregate"]["guarded"],
                challenger["aggregate"]["guarded"],
            ),
            "guard_dependency": {
                "baseline": baseline["aggregate"]["guard_dependency"],
                "challenger": challenger["aggregate"]["guard_dependency"],
            },
        },
        "sources": source_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-compare-closed-loop-arena")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("challenger", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    result = compare_arena_artifacts(
        json.loads(args.baseline.read_text(encoding="utf-8")),
        json.loads(args.challenger.read_text(encoding="utf-8")),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
