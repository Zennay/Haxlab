from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any


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


def _validated_sha256(
    failures: list[str],
    *,
    path: Any,
    label: str,
) -> str | None:
    if not isinstance(path, Path):
        failures.append(f"provenance:{label}:path:not_path")
        return None
    try:
        if not path.is_file():
            failures.append(f"provenance:{label}:path:not_regular_file")
            return None
        return _sha256(path)
    except (OSError, ValueError) as exc:
        failures.append(
            f"provenance:{label}:sha256:unreadable:{type(exc).__name__}"
        )
        return None


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


def _validate_summary_against_rows(
    failures: list[str],
    *,
    summary: Any,
    rows: list[Any],
    label: str,
) -> None:
    if not isinstance(summary, dict):
        failures.append(f"{label}:summary:not_object")
        return

    outcomes = {"win": 0, "draw": 0, "loss": 0}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            failures.append(f"{label}:row:{index}:not_object")
            continue
        result = row.get("result")
        if result not in outcomes:
            failures.append(f"{label}:row:{index}:invalid_result:{result!r}")
            continue
        outcomes[result] += 1

    matches = len(rows)
    expected = {
        "matches": matches,
        "wins": outcomes["win"],
        "draws": outcomes["draw"],
        "losses": outcomes["loss"],
    }
    for key, value in expected.items():
        _expect_exact(failures, summary, key, value, f"{label}:summary:{key}")

    expected_score = (
        (outcomes["win"] + 0.5 * outcomes["draw"]) / matches
        if matches
        else 0.0
    )
    score = summary.get("proxy_match_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        failures.append(f"{label}:summary:proxy_match_score:non_numeric")
    elif not math.isfinite(float(score)) or not math.isclose(
        float(score),
        expected_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        failures.append(
            f"{label}:summary:proxy_match_score:mismatch:"
            f"{score!r}!={expected_score!r}"
        )


def _validate_raw_match_grid(
    failures: list[str],
    *,
    team_rows: list[Any],
    plug_rows: list[Any],
    max_scenarios: int,
    plug_repeats: int,
) -> None:
    expected_team = {
        (scenario, side, 0)
        for scenario in range(1, max_scenarios + 1)
        for side in (1, 2)
    }
    actual_team: set[tuple[Any, Any, Any]] = set()
    for index, row in enumerate(team_rows):
        if not isinstance(row, dict):
            continue
        _expect_exact(failures, row, "mode", "full_team", f"team:row:{index}:mode")
        _expect_exact(failures, row, "tested_role", None, f"team:row:{index}:tested_role")
        actual_team.add(
            (
                row.get("scenario_index"),
                row.get("test_team_id"),
                row.get("repeat_index"),
            )
        )
    if actual_team != expected_team:
        failures.append("match_results:team_mode:grid_mismatch")

    expected_plug = {
        (role, scenario, side, repeat)
        for role in ROLES
        for scenario in range(1, max_scenarios + 1)
        for side in (1, 2)
        for repeat in range(plug_repeats)
    }
    actual_plug: set[tuple[Any, Any, Any, Any]] = set()
    for index, row in enumerate(plug_rows):
        if not isinstance(row, dict):
            continue
        _expect_exact(
            failures,
            row,
            "mode",
            "plug_and_play",
            f"plug:row:{index}:mode",
        )
        actual_plug.add(
            (
                row.get("tested_role"),
                row.get("scenario_index"),
                row.get("test_team_id"),
                row.get("repeat_index"),
            )
        )
    if actual_plug != expected_plug:
        failures.append("match_results:plug_and_play:grid_mismatch")


def _validate_reuse_request(
    failures: list[str],
    *,
    seconds: Any,
    sample_every: Any,
    max_scenarios: Any,
    plug_repeats: Any,
    seed: Any,
) -> None:
    for key, value in (
        ("seconds", seconds),
        ("sample_every", sample_every),
        ("max_scenarios", max_scenarios),
        ("plug_repeats", plug_repeats),
    ):
        if isinstance(value, bool) or not isinstance(value, int):
            failures.append(f"request:{key}:not_native_integer")
        elif value < 1:
            failures.append(f"request:{key}:below_minimum")

    if isinstance(seed, bool) or not isinstance(seed, int):
        failures.append("request:seed:not_native_integer")


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

    _validate_reuse_request(
        failures,
        seconds=seconds,
        sample_every=sample_every,
        max_scenarios=max_scenarios,
        plug_repeats=plug_repeats,
        seed=seed,
    )
    if isinstance(partners, (str, bytes)) or not isinstance(partners, Sequence):
        failures.append("request:partners:not_sequence")
    elif not partners:
        failures.append("request:partners:empty")
    if failures:
        return tuple(failures)

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

    bound_input_failures_before = len(failures)
    challenger_sha256 = _validated_sha256(
        failures, path=challenger, label="challenger"
    )
    champion_sha256 = _validated_sha256(
        failures, path=champion, label="champion"
    )
    partner_sha256s = [
        _validated_sha256(failures, path=path, label=f"partner:{index}")
        for index, path in enumerate(partners)
    ]
    stadium_sha256 = _validated_sha256(failures, path=stadium, label="stadium")
    scenarios_sha256 = _validated_sha256(
        failures, path=scenarios, label="scenarios"
    )
    if len(failures) > bound_input_failures_before:
        return tuple(failures)

    expected_provenance = {
        "challenger_model": str(challenger),
        "champion_model": str(champion),
        "partner_models": [str(path) for path in partners],
        "stadium": str(stadium),
        "scenarios": str(scenarios),
    }
    if challenger_sha256 is not None:
        expected_provenance["challenger_sha256"] = challenger_sha256
    if champion_sha256 is not None:
        expected_provenance["champion_sha256"] = champion_sha256
    if all(value is not None for value in partner_sha256s):
        expected_provenance["partner_sha256s"] = partner_sha256s
    if stadium_sha256 is not None:
        expected_provenance["stadium_sha256"] = stadium_sha256
    if scenarios_sha256 is not None:
        expected_provenance["scenarios_sha256"] = scenarios_sha256

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
        team_rows = []
    elif len(team_rows) != expected_team_rows:
        failures.append(
            f"match_results:team_mode:count:{len(team_rows)}!={expected_team_rows}"
        )
    if not isinstance(plug_rows, list):
        failures.append("match_results:plug_and_play:not_list")
        plug_rows = []
    elif len(plug_rows) != expected_plug_rows:
        failures.append(
            f"match_results:plug_and_play:count:{len(plug_rows)}!={expected_plug_rows}"
        )

    _validate_raw_match_grid(
        failures,
        team_rows=team_rows,
        plug_rows=plug_rows,
        max_scenarios=max_scenarios,
        plug_repeats=plug_repeats,
    )

    team_mode = payload.get("team_mode")
    if not isinstance(team_mode, dict):
        failures.append("team_mode:not_object")
        team_mode = {}
    _validate_summary_against_rows(
        failures,
        summary=team_mode.get("summary"),
        rows=team_rows,
        label="team",
    )

    plug = payload.get("plug_and_play")
    if not isinstance(plug, dict):
        failures.append("plug_and_play:not_object")
        plug = {}
    _validate_summary_against_rows(
        failures,
        summary=plug.get("summary"),
        rows=plug_rows,
        label="plug",
    )

    by_role = plug.get("by_role")
    if not isinstance(by_role, dict):
        failures.append("plug_and_play:by_role:not_object")
        by_role = {}
    for role in ROLES:
        role_row = by_role.get(role)
        if not isinstance(role_row, dict):
            failures.append(f"plug_and_play:by_role:{role}:not_object")
            role_row = {}
        role_matches = [
            row
            for row in plug_rows
            if isinstance(row, dict) and row.get("tested_role") == role
        ]
        _validate_summary_against_rows(
            failures,
            summary=role_row.get("team_outcome"),
            rows=role_matches,
            label=f"plug:{role}",
        )

    return tuple(failures)


def reusable_result_or_reasons(
    result_path: Path,
    **kwargs: Any,
) -> tuple[bool, tuple[str, ...]]:
    if not isinstance(result_path, Path):
        return False, ("result:path:not_path",)
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
