import json

import pytest

from haxlab.evaluation.evidence_io import (
    load_evaluation_evidence,
    parse_evaluation_evidence,
)


def _payload(**overrides):
    value = {
        "challenger_id": "model-2",
        "champion_id": "model-1",
        "games_vs_champion": 1000,
        "score_rate_vs_champion": 0.55,
        "score_rate_lower_bound": 0.52,
        "goal_difference_per_game": 0.2,
        "frozen_scenarios_total": 100,
        "frozen_scenarios_passed": 100,
        "regressions": [],
        "reproducible": True,
    }
    value.update(overrides)
    return value


def test_valid_payload_builds_evaluation_evidence() -> None:
    result = parse_evaluation_evidence(_payload())

    assert result.valid
    assert result.reasons == ()
    assert result.evidence is not None
    assert result.evidence.challenger_id == "model-2"
    assert result.evidence.games_vs_champion == 1000


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("challenger_id", 2, "invalid_evidence:challenger_id:not_string"),
        ("champion_id", " ", "invalid_evidence:champion_id:empty"),
        (
            "games_vs_champion",
            "1000",
            "invalid_evidence:games_vs_champion:not_integer",
        ),
        (
            "games_vs_champion",
            True,
            "invalid_evidence:games_vs_champion:not_integer",
        ),
        (
            "score_rate_vs_champion",
            "0.55",
            "invalid_evidence:score_rate_vs_champion:not_number",
        ),
        (
            "score_rate_lower_bound",
            float("nan"),
            "invalid_evidence:score_rate_lower_bound:non_finite",
        ),
        (
            "reproducible",
            "true",
            "invalid_evidence:reproducible:not_boolean",
        ),
    ],
)
def test_coercible_or_non_native_fields_fail_closed(
    field: str, value: object, reason: str
) -> None:
    result = parse_evaluation_evidence(_payload(**{field: value}))

    assert not result.valid
    assert result.evidence is None
    assert reason in result.reasons


def test_missing_and_unexpected_fields_fail_closed() -> None:
    payload = _payload(extra="unexpected")
    del payload["games_vs_champion"]

    result = parse_evaluation_evidence(payload)

    assert not result.valid
    assert "invalid_evidence:missing:games_vs_champion" in result.reasons
    assert "invalid_evidence:unexpected:extra" in result.reasons


def test_cross_field_invariants_fail_closed() -> None:
    result = parse_evaluation_evidence(
        _payload(
            champion_id="model-2",
            score_rate_vs_champion=0.51,
            score_rate_lower_bound=0.52,
            frozen_scenarios_total=10,
            frozen_scenarios_passed=11,
        )
    )

    assert not result.valid
    assert "invalid_evidence:challenger_matches_champion" in result.reasons
    assert (
        "invalid_evidence:score_rate_lower_bound_exceeds_score_rate"
        in result.reasons
    )
    assert (
        "invalid_evidence:frozen_scenarios_passed_exceeds_total:11>10"
        in result.reasons
    )


def test_regression_rows_require_exact_native_shape() -> None:
    result = parse_evaluation_evidence(
        _payload(
            regressions=[
                {
                    "scenario": "kickoff",
                    "severity": "critical",
                    "details": "regressed",
                },
                {
                    "scenario": "kickoff",
                    "severity": "critical",
                    "details": "regressed",
                },
                {
                    "scenario": 7,
                    "severity": "warning",
                    "details": [],
                    "extra": "nope",
                },
            ]
        )
    )

    assert not result.valid
    assert "invalid_evidence:regressions:1:duplicate" in result.reasons
    assert "invalid_evidence:regressions:2:scenario:not_string" in result.reasons
    assert "invalid_evidence:regressions:2:details:not_string" in result.reasons
    assert "invalid_evidence:regressions:2:unexpected:extra" in result.reasons


def test_regressions_must_be_json_list_not_tuple() -> None:
    result = parse_evaluation_evidence(_payload(regressions=()))

    assert not result.valid
    assert result.reasons == ("invalid_evidence:regressions:not_list",)


def test_loader_rejects_invalid_json(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text("{", encoding="utf-8")

    result = load_evaluation_evidence(path)

    assert not result.valid
    assert result.reasons == (
        "invalid_evidence:path:unreadable_or_invalid_json",
    )


def test_loader_rejects_symlink(tmp_path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps(_payload()), encoding="utf-8")
    link = tmp_path / "evidence.json"
    link.symlink_to(source)

    result = load_evaluation_evidence(link)

    assert not result.valid
    assert result.reasons == ("invalid_evidence:path:symlink",)


def test_loader_parses_regular_file(tmp_path) -> None:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")

    result = load_evaluation_evidence(path)

    assert result.valid
    assert result.evidence is not None
    assert result.evidence.champion_id == "model-1"
