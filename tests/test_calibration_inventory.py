from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import haxlab.evaluation.calibration_inventory as inventory_module
from haxlab.evaluation.calibration_inventory import (
    INVENTORY_SCHEMA,
    inventory_calibration_results,
    main,
)


def _request(tmp_path: Path) -> dict[str, object]:
    result_root = tmp_path / "results"
    source_root = tmp_path / "sources"
    result_root.mkdir()
    source_root.mkdir()

    for source_id in ("01", "02", "03"):
        source = source_root / f"source-{source_id}"
        source.mkdir()
        (source / "stadium.hbs").write_text(
            f"stadium-{source_id}\n",
            encoding="utf-8",
        )
        (source / "scenarios.json").write_text(
            '{"scenarios": []}\n',
            encoding="utf-8",
        )

    champion = tmp_path / "champion.json"
    candidate_d = tmp_path / "candidate-d.json"
    weak_zero = tmp_path / "weak-zero.json"
    partner_b = tmp_path / "partner-b.json"
    partner_c = tmp_path / "partner-c.json"
    for path in (champion, candidate_d, weak_zero, partner_b, partner_c):
        path.write_text(f"{path.stem}\n", encoding="utf-8")

    return {
        "result_root": result_root,
        "source_root": source_root,
        "challengers": {
            "champion-self": champion,
            "candidate-d": candidate_d,
            "weak-zero": weak_zero,
        },
        "champion": champion,
        "partners": (champion, partner_b, partner_c),
        "seconds": 30,
        "sample_every": 6,
        "max_scenarios": 16,
        "plug_repeats": 1,
        "seed": 1337,
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_payload(
    request: dict[str, object],
    *,
    label: str,
    source_id: str,
) -> dict[str, object]:
    challengers = request["challengers"]
    champion = request["champion"]
    partners = request["partners"]
    source_root = request["source_root"]
    assert isinstance(challengers, dict)
    assert isinstance(champion, Path)
    assert isinstance(partners, tuple)
    assert isinstance(source_root, Path)
    challenger = challengers[label]
    assert isinstance(challenger, Path)
    source = source_root / f"source-{source_id}"
    stadium = source / "stadium.hbs"
    scenarios = source / "scenarios.json"

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


def test_inventory_classifies_all_nine_sources_in_stable_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)
    calls: list[tuple[Path, dict[str, object]]] = []

    def fake_validator(result_path: Path, **kwargs):
        calls.append((result_path, kwargs))
        if result_path.name == "weak-zero-source-03.json":
            return False, ("synthetic_blocker",)
        return True, ()

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        fake_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["schema"] == INVENTORY_SCHEMA
    assert report["expected_results"] == 9
    assert report["reusable_results"] == 8
    assert report["blocked_results"] == 1
    assert report["complete"] is False
    assert [
        (row["label"], row["source_id"])
        for row in report["results"]
    ] == [
        (label, source_id)
        for label in ("champion-self", "candidate-d", "weak-zero")
        for source_id in ("01", "02", "03")
    ]
    assert report["labels"]["champion-self"]["reusable_sources"] == 3
    assert report["labels"]["candidate-d"]["reusable_sources"] == 3
    assert report["labels"]["weak-zero"]["reusable_sources"] == 2
    assert report["results"][-1]["reasons"] == ["synthetic_blocker"]

    assert len(calls) == 9
    challengers = request["challengers"]
    source_root = request["source_root"]
    assert isinstance(challengers, dict)
    assert isinstance(source_root, Path)
    for result_path, kwargs in calls:
        label, source_suffix = result_path.stem.rsplit("-source-", 1)
        assert kwargs["challenger"] == challengers[label]
        source = source_root / f"source-{source_suffix}"
        assert kwargs["stadium"] == source / "stadium.hbs"
        assert kwargs["scenarios"] == source / "scenarios.json"


def test_inventory_composes_with_real_resume_validator(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)
    result_root = request["result_root"]
    assert isinstance(result_root, Path)
    target = result_root / "champion-self-source-01.json"
    target.write_text(
        json.dumps(
            _valid_payload(
                request,
                label="champion-self",
                source_id="01",
            )
        ),
        encoding="utf-8",
    )

    real_validator = inventory_module.reusable_result_or_reasons

    def validator(result_path: Path, **kwargs):
        if result_path == target:
            return real_validator(result_path, **kwargs)
        return True, ()

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        validator,
    )

    report = inventory_calibration_results(**request)

    assert report["complete"] is True
    target_row = next(
        row for row in report["results"] if row["result"] == str(target)
    )
    assert target_row["reusable"] is True
    assert target_row["reasons"] == []


def test_missing_challenger_blocks_only_its_three_sources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)
    challengers = dict(request["challengers"])
    challengers.pop("weak-zero")
    request["challengers"] = challengers
    calls = 0

    def fake_validator(result_path: Path, **kwargs):
        nonlocal calls
        calls += 1
        return True, ()

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        fake_validator,
    )

    report = inventory_calibration_results(**request)

    assert calls == 6
    assert report["reusable_results"] == 6
    assert report["blocked_results"] == 3
    assert report["request_errors"] == ["challengers:missing:weak-zero"]
    weak_rows = [
        row for row in report["results"] if row["label"] == "weak-zero"
    ]
    assert all(
        row["reasons"] == ["challenger:weak-zero:missing"]
        for row in weak_rows
    )


def test_non_string_challenger_key_fails_closed_without_coercion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)

    class HostileKey:
        def __str__(self) -> str:
            raise AssertionError("challenger keys must not be string-coerced")

    challengers = dict(request["challengers"])
    challengers[HostileKey()] = request["champion"]
    request["challengers"] = challengers

    def unexpected_validator(*args, **kwargs):
        raise AssertionError("invalid challenger mapping must not reach validator")

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        unexpected_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["request_errors"] == ["challengers:key_type"]
    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert all(
        row["reasons"] == ["challengers:key_type"]
        for row in report["results"]
    )


def test_invalid_bound_input_types_block_before_delegation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)
    request["champion"] = "not-a-path"
    partners = list(request["partners"])
    partners[1] = object()
    request["partners"] = partners

    def unexpected_validator(*args, **kwargs):
        raise AssertionError("invalid bound inputs must not reach validator")

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        unexpected_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["request_errors"] == [
        "champion:not_path",
        "partners:1:not_path",
    ]
    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert all(
        row["reasons"]
        == ["champion:not_path", "partners:1:not_path"]
        for row in report["results"]
    )


@pytest.mark.parametrize(
    ("validator_result", "expected_reason"),
    [
        (("yes", ()), "validator_error:reusable_not_boolean"),
        ((True, ["reason"]), "validator_error:reasons_shape"),
        ((False, ()), "validator_error:blocked_without_reasons"),
    ],
)
def test_inconsistent_validator_result_contract_fails_closed(
    tmp_path: Path,
    monkeypatch,
    validator_result,
    expected_reason: str,
) -> None:
    request = _request(tmp_path)

    def inconsistent_validator(result_path: Path, **kwargs):
        return validator_result

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        inconsistent_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert all(
        row["reasons"][0] == expected_reason
        for row in report["results"]
    )


def test_missing_results_are_reported_fail_closed_without_crashing(
    tmp_path: Path,
) -> None:
    report = inventory_calibration_results(**_request(tmp_path))

    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert report["complete"] is False
    assert all(
        row["reasons"] == ["result:unreadable:FileNotFoundError"]
        for row in report["results"]
    )


def test_validator_input_error_is_converted_to_blocked_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)

    def failing_validator(result_path: Path, **kwargs):
        raise FileNotFoundError("bound model disappeared")

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        failing_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert all(
        row["reasons"] == ["validator_error:FileNotFoundError"]
        for row in report["results"]
    )


def test_inconsistent_validator_success_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)

    def inconsistent_validator(result_path: Path, **kwargs):
        return True, ("unexpected_reason",)

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        inconsistent_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["reusable_results"] == 0
    assert report["blocked_results"] == 9
    assert all(
        row["reasons"]
        == [
            "validator_error:reusable_with_reasons",
            "unexpected_reason",
        ]
        for row in report["results"]
    )


def test_request_bindings_are_snapshotted_before_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    request = _request(tmp_path)
    challengers = dict(request["challengers"])
    partners = list(request["partners"])
    request["challengers"] = challengers
    request["partners"] = partners
    original_candidate = challengers["candidate-d"]
    original_partner = partners[1]
    calls: list[tuple[Path, dict[str, object]]] = []

    def mutating_validator(result_path: Path, **kwargs):
        calls.append((result_path, kwargs))
        if len(calls) == 1:
            challengers["candidate-d"] = challengers["weak-zero"]
            partners[1] = challengers["weak-zero"]
        return True, ()

    monkeypatch.setattr(
        inventory_module,
        "reusable_result_or_reasons",
        mutating_validator,
    )

    report = inventory_calibration_results(**request)

    assert report["complete"] is True
    candidate_calls = [
        kwargs
        for result_path, kwargs in calls
        if result_path.name.startswith("candidate-d-source-")
    ]
    assert len(candidate_calls) == 3
    assert all(
        kwargs["challenger"] == original_candidate
        for kwargs in candidate_calls
    )
    assert all(
        kwargs["partners"][1] == original_partner
        for _, kwargs in calls
    )


def test_cli_require_complete_accepts_real_nine_of_nine_inventory(
    tmp_path: Path,
    capsys,
) -> None:
    request = _request(tmp_path)
    result_root = request["result_root"]
    champion = request["champion"]
    challengers = request["challengers"]
    partners = request["partners"]
    assert isinstance(result_root, Path)
    assert isinstance(champion, Path)
    assert isinstance(challengers, dict)
    assert isinstance(partners, tuple)

    for label in ("champion-self", "candidate-d", "weak-zero"):
        for source_id in ("01", "02", "03"):
            path = result_root / f"{label}-source-{source_id}.json"
            path.write_text(
                json.dumps(
                    _valid_payload(
                        request,
                        label=label,
                        source_id=source_id,
                    )
                ),
                encoding="utf-8",
            )

    argv = [
        str(result_root),
        "--source-root",
        str(request["source_root"]),
        "--champion",
        str(champion),
        "--candidate-d",
        str(challengers["candidate-d"]),
        "--weak-zero",
        str(challengers["weak-zero"]),
    ]
    for partner in partners:
        argv.extend(["--partner-model", str(partner)])
    argv.extend(
        [
            "--seconds",
            "30",
            "--sample-every",
            "6",
            "--max-scenarios",
            "16",
            "--plug-repeats",
            "1",
            "--seed",
            "1337",
            "--require-complete",
        ]
    )

    assert main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert payload["expected_results"] == 9
    assert payload["reusable_results"] == 9
    assert payload["blocked_results"] == 0
    assert all(row["reusable"] is True for row in payload["results"])


def test_cli_require_complete_returns_nonzero_for_partial_inventory(
    tmp_path: Path,
    capsys,
) -> None:
    request = _request(tmp_path)
    champion = request["champion"]
    challengers = request["challengers"]
    partners = request["partners"]
    assert isinstance(champion, Path)
    assert isinstance(challengers, dict)
    assert isinstance(partners, tuple)

    argv = [
        str(request["result_root"]),
        "--source-root",
        str(request["source_root"]),
        "--champion",
        str(champion),
        "--candidate-d",
        str(challengers["candidate-d"]),
        "--weak-zero",
        str(challengers["weak-zero"]),
    ]
    for partner in partners:
        argv.extend(["--partner-model", str(partner)])
    argv.extend(
        [
            "--seconds",
            "30",
            "--sample-every",
            "6",
            "--max-scenarios",
            "16",
            "--plug-repeats",
            "1",
            "--seed",
            "1337",
            "--require-complete",
        ]
    )

    assert main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == INVENTORY_SCHEMA
    assert payload["complete"] is False
    assert payload["blocked_results"] == 9
