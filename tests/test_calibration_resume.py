from __future__ import annotations

import hashlib
import json
from pathlib import Path

from haxlab.evaluation.calibration_resume import reusable_result_or_reasons


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(tmp_path: Path) -> dict[str, object]:
    challenger = tmp_path / "challenger.json"
    champion = tmp_path / "champion.json"
    partner_b = tmp_path / "partner-b.json"
    partner_c = tmp_path / "partner-c.json"
    stadium = tmp_path / "stadium.hbs"
    scenarios = tmp_path / "scenarios.json"
    for path, text in (
        (challenger, "challenger\n"),
        (champion, "champion\n"),
        (partner_b, "partner-b\n"),
        (partner_c, "partner-c\n"),
        (stadium, "stadium\n"),
        (scenarios, '{"scenarios": []}\n'),
    ):
        path.write_text(text, encoding="utf-8")
    return {
        "challenger": challenger,
        "champion": champion,
        "partners": (champion, partner_b, partner_c),
        "stadium": stadium,
        "scenarios": scenarios,
        "seconds": 30,
        "sample_every": 6,
        "max_scenarios": 16,
        "plug_repeats": 1,
        "seed": 1337,
    }


def _payload(inputs: dict[str, object]) -> dict[str, object]:
    challenger = inputs["challenger"]
    champion = inputs["champion"]
    partners = inputs["partners"]
    stadium = inputs["stadium"]
    scenarios = inputs["scenarios"]
    assert isinstance(challenger, Path)
    assert isinstance(champion, Path)
    assert isinstance(partners, tuple)
    assert isinstance(stadium, Path)
    assert isinstance(scenarios, Path)
    team_rows = [
        {
            "mode": "full_team",
            "tested_role": None,
            "scenario_index": scenario,
            "test_team_id": side,
            "repeat_index": 0,
            "result": "draw",
        }
        for scenario in range(1, 17)
        for side in (1, 2)
    ]
    plug_rows = [
        {
            "mode": "plug_and_play",
            "tested_role": role,
            "scenario_index": scenario,
            "test_team_id": side,
            "repeat_index": 0,
            "result": "draw",
        }
        for role in ("gk", "dm", "am", "st")
        for scenario in range(1, 17)
        for side in (1, 2)
    ]
    return {
        "schema": "haxlab-closed-loop-arena-v2",
        "evaluation_mode": (
            "paired_raw_policy_full_team_plus_plug_and_play_context_generalization_v2"
        ),
        "raw_policy_only": True,
        "safety_recovery_enabled": False,
        "paired_reference_design": True,
        "provenance": {
            "challenger_model": str(challenger),
            "challenger_sha256": _sha(challenger),
            "champion_model": str(champion),
            "champion_sha256": _sha(champion),
            "partner_models": [str(path) for path in partners],
            "partner_sha256s": [_sha(path) for path in partners],
            "stadium": str(stadium),
            "stadium_sha256": _sha(stadium),
            "scenarios": str(scenarios),
            "scenarios_sha256": _sha(scenarios),
        },
        "config": {
            "seconds": 30,
            "sample_every": 6,
            "max_scenarios": 16,
            "plug_repeats": 1,
            "seed": 1337,
            "scenario_count": 16,
            "roles": ["gk", "dm", "am", "st"],
            "pair_tie_margin": 0.025,
        },
        "team_mode": {
            "summary": {
                "matches": 32,
                "wins": 0,
                "draws": 32,
                "losses": 0,
                "proxy_match_score": 0.5,
            }
        },
        "plug_and_play": {
            "summary": {
                "matches": 128,
                "wins": 0,
                "draws": 128,
                "losses": 0,
                "proxy_match_score": 0.5,
            },
            "by_role": {
                role: {
                    "team_outcome": {
                        "matches": 32,
                        "wins": 0,
                        "draws": 32,
                        "losses": 0,
                        "proxy_match_score": 0.5,
                    }
                }
                for role in ("gk", "dm", "am", "st")
            },
        },
        "match_results": {
            "team_mode": team_rows,
            "plug_and_play": plug_rows,
        },
    }


def _write_result(tmp_path: Path, payload: dict[str, object]) -> Path:
    result = tmp_path / "result.json"
    result.write_text(json.dumps(payload), encoding="utf-8")
    return result


def test_accepts_exact_same_input_contract(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, _payload(inputs)),
        **inputs,
    )
    assert reusable is True
    assert reasons == ()


def test_rejects_non_positive_reuse_runtime_contract(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    config = payload["config"]
    assert isinstance(config, dict)

    for key in ("seconds", "sample_every"):
        mutated_inputs = dict(inputs)
        mutated_inputs[key] = 0
        mutated_payload = json.loads(json.dumps(payload))
        mutated_config = mutated_payload["config"]
        assert isinstance(mutated_config, dict)
        mutated_config[key] = 0

        reusable, reasons = reusable_result_or_reasons(
            _write_result(tmp_path, mutated_payload),
            **mutated_inputs,
        )

        assert reusable is False
        assert f"request:{key}:below_minimum" in reasons


def test_rejects_boolean_reuse_request_contract(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    config = payload["config"]
    assert isinstance(config, dict)
    config["seconds"] = True
    inputs["seconds"] = True

    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, payload),
        **inputs,
    )

    assert reusable is False
    assert "request:seconds:not_native_integer" in reasons


def test_rejects_changed_model_bytes(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = _write_result(tmp_path, _payload(inputs))
    challenger = inputs["challenger"]
    assert isinstance(challenger, Path)
    challenger.write_text("changed\n", encoding="utf-8")

    reusable, reasons = reusable_result_or_reasons(result, **inputs)

    assert reusable is False
    assert any("provenance:challenger_sha256:mismatch" in reason for reason in reasons)


def test_rejects_changed_frozen_scenario_bytes(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = _write_result(tmp_path, _payload(inputs))
    scenarios = inputs["scenarios"]
    assert isinstance(scenarios, Path)
    scenarios.write_text('{"scenarios": [1]}\n', encoding="utf-8")

    reusable, reasons = reusable_result_or_reasons(result, **inputs)

    assert reusable is False
    assert any("provenance:scenarios_sha256:mismatch" in reason for reason in reasons)


def test_rejects_config_or_match_cardinality_drift(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    config = payload["config"]
    matches = payload["match_results"]
    assert isinstance(config, dict)
    assert isinstance(matches, dict)
    config["seed"] = 1338
    matches["team_mode"] = [{} for _ in range(31)]

    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, payload),
        **inputs,
    )

    assert reusable is False
    assert "config:seed:mismatch:1338!=1337" in reasons
    assert "match_results:team_mode:count:31!=32" in reasons


def test_rejects_boolean_numeric_config_coercion(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    config = payload["config"]
    assert isinstance(config, dict)
    config["plug_repeats"] = True

    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, payload),
        **inputs,
    )

    assert reusable is False
    assert "config:plug_repeats:mismatch:True!=1" in reasons




def test_rejects_raw_outcome_summary_tampering(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    matches = payload["match_results"]
    assert isinstance(matches, dict)
    team_rows = matches["team_mode"]
    assert isinstance(team_rows, list)
    first = team_rows[0]
    assert isinstance(first, dict)
    first["result"] = "win"

    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, payload),
        **inputs,
    )

    assert reusable is False
    assert any("team:summary:wins:mismatch" in reason for reason in reasons)
    assert any(
        "team:summary:proxy_match_score:mismatch" in reason for reason in reasons
    )


def test_rejects_duplicate_match_grid_even_when_count_matches(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    payload = _payload(inputs)
    matches = payload["match_results"]
    assert isinstance(matches, dict)
    plug_rows = matches["plug_and_play"]
    assert isinstance(plug_rows, list)
    plug_rows[-1] = dict(plug_rows[0])

    reusable, reasons = reusable_result_or_reasons(
        _write_result(tmp_path, payload),
        **inputs,
    )

    assert reusable is False
    assert "match_results:plug_and_play:grid_mismatch" in reasons

def test_rejects_corrupt_or_partial_result(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = tmp_path / "result.json"
    result.write_text('{"schema": ', encoding="utf-8")

    reusable, reasons = reusable_result_or_reasons(result, **inputs)

    assert reusable is False
    assert len(reasons) == 1
    assert reasons[0].startswith("result:unreadable:")


def test_rejects_missing_or_non_file_bound_inputs_without_raising(
    tmp_path: Path,
) -> None:
    for key, as_directory in (("challenger", False), ("scenarios", True)):
        case_dir = tmp_path / key
        case_dir.mkdir()
        inputs = _inputs(case_dir)
        result = _write_result(case_dir, _payload(inputs))
        bound_path = inputs[key]
        assert isinstance(bound_path, Path)
        bound_path.unlink()
        if as_directory:
            bound_path.mkdir()

        reusable, reasons = reusable_result_or_reasons(result, **inputs)

        assert reusable is False
        assert f"provenance:{key}:path:not_regular_file" in reasons


def test_rejects_non_path_bound_input_without_raising(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    result = _write_result(tmp_path, _payload(inputs))
    inputs["stadium"] = "stadium.hbs"

    reusable, reasons = reusable_result_or_reasons(result, **inputs)

    assert reusable is False
    assert "provenance:stadium:path:not_path" in reasons

