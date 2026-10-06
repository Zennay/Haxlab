from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence


ARENA_SCHEMA = "haxlab-closed-loop-arena-v2"
EVALUATION_MODE = "paired_raw_policy_full_team_plus_plug_and_play_context_generalization_v2"
ROLES = ("gk", "dm", "am", "st")
PAIR_TIE_MARGIN = 0.025


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expect_exact(
    failures: list[str],
    mapping: dict[str, Any],
    key: str,
    expected: Any,
    label: str,
) -> None:
    if key not in mapping:
        failures.append(f"{label}:missing")
        return
    actual = mapping[key]
    if type(actual) is not type(expected) or actual != expected:
        failures.append(f"{label}:mismatch:{actual!r}!={expected!r}")


def validate_reusable_result(
    payload: Any,
    *,
    challenger: Path,
    champion: Path,
    partners: Sequence[Path],
    stadium: Path,
    scenarios: Path,
    seconds: int,
    sample_every: int,
    max_scenarios: int,
    plug_repeats: int,
    seed: int,
) -> tuple[str, ...]:
    failures: list[str] = []
    if not isinstance(payload, dict):
        return ("result:not_object",)

    _expect_exact(failures, payload, "schema", ARENA_SCHEMA, "schema")
    _expect_exact(
        failures,
        payload,
        "evaluation_mode",
        EVALUATION_MODE,
        "evaluation_mode",
    )
    _expect_exact(failures, payload, "raw_policy_only", True, "raw_policy_only")
    _expect_exact(
        failures,
        payload,
        "safety_recovery_enabled",
        False,
        "safety_recovery_enabled",
    )
    _expect_exact(
        failures,
        payload,
        "paired_reference_design",
        True,
        "paired_reference_design",
    )

    provenance = payload.get("provenance")
    if not isinstance(provenance, dict):
        failures.append("provenance:not_object")
        provenance = {}

    expected_provenance = {
        "challenger_model": str(challenger),
        "challenger_sha256": _sha256(challenger),
        "champion_model": str(champion),
        "champion_sha256": _sha256(champion),
        "partner_models": [str(path) for path in partners],
        "partner_sha256s": [_sha256(path) for path in partners],
        "stadium": str(stadium),
        "stadium_sha256": _sha256(stadium),
        "scenarios": str(scenarios),
        "scenarios_sha256": _sha256(scenarios),
    }
    for key, expected in expected_provenance.items():
        _expect_exact(failures, provenance, key, expected, f"provenance:{key}")

    config = payload.get("config")
    if not isinstance(config, dict):
        failures.append("config:not_object")
        config = {}
    expected_config = {
        "seconds": seconds,
        "sample_every": sample_every,
        "max_scenarios": max_scenarios,
        "plug_repeats": plug_repeats,
        "seed": seed,
        "scenario_count": max_scenarios,
        "roles": list(ROLES),
        "pair_tie_margin": PAIR_TIE_MARGIN,
    }
    for key, expected in expected_config.items():
        _expect_exact(failures, config, key, expected, f"config:{key}")

    matches = payload.get("match_results")
    if not isinstance(matches, dict):
        failures.append("match_results:not_object")
        matches = {}
    team_rows = matches.get("team_mode")
    plug_rows = matches.get("plug_and_play")
    expected_team_rows = max_scenarios * 2
    expected_plug_rows = max_scenarios * 2 * len(ROLES) * plug_repeats
    if not isinstance(team_rows, list):
        failures.append("match_results:team_mode:not_list")
    elif len(team_rows) != expected_team_rows:
        failures.append(
            f"match_results:team_mode:count:{len(team_rows)}!={expected_team_rows}"
        )
    if not isinstance(plug_rows, list):
        failures.append("match_results:plug_and_play:not_list")
    elif len(plug_rows) != expected_plug_rows:
        failures.append(
            f"match_results:plug_and_play:count:{len(plug_rows)}!={expected_plug_rows}"
        )

    return tuple(failures)


def reusable_result_or_reasons(
    result_path: Path,
    **kwargs: Any,
) -> tuple[bool, tuple[str, ...]]:
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return False, (f"result:unreadable:{type(exc).__name__}",)
    failures = validate_reusable_result(payload, **kwargs)
    return not failures, failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail-closed validator for reusing same-SHA Arena calibration results."
    )
    parser.add_argument("result", type=Path)
    parser.add_argument("--challenger", required=True, type=Path)
    parser.add_argument("--champion", required=True, type=Path)
    parser.add_argument("--partner-model", action="append", required=True, type=Path)
    parser.add_argument("--stadium", required=True, type=Path)
    parser.add_argument("--scenarios", required=True, type=Path)
    parser.add_argument("--seconds", required=True, type=int)
    parser.add_argument("--sample-every", required=True, type=int)
    parser.add_argument("--max-scenarios", required=True, type=int)
    parser.add_argument("--plug-repeats", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    reusable, reasons = reusable_result_or_reasons(
        args.result,
        challenger=args.challenger,
        champion=args.champion,
        partners=tuple(args.partner_model),
        stadium=args.stadium,
        scenarios=args.scenarios,
        seconds=args.seconds,
        sample_every=args.sample_every,
        max_scenarios=args.max_scenarios,
        plug_repeats=args.plug_repeats,
        seed=args.seed,
    )
    if reusable:
        print(f"ARENA_CALIBRATION_REUSE_VALID result={args.result}")
        return 0
    for reason in reasons:
        print(f"ARENA_CALIBRATION_REUSE_REJECT reason={reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
