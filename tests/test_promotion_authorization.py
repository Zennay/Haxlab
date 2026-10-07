from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.evaluation.models import PromotionDecision
from haxlab.evaluation.promotion_authorization import (
    GATE_RECEIPT_SCHEMA,
    PromotionAuthorizationError,
    REQUIRED_GATES,
    SCHEMA_VERSION,
    authorize_promotion,
    load_authorization_request,
    main,
    render_authorization,
)


HEAD = "a" * 40
EVIDENCE_SHA = "b" * 64
POLICY_SHA = "c" * 64


def gate_receipt():
    return {
        "schema": GATE_RECEIPT_SCHEMA,
        "exact_head": HEAD,
        "mandatory_gates_green": True,
        "gates": [
            {
                "gate": gate,
                "workflow_path": workflow_path,
                "workflow_name": workflow_name,
                "head_sha": HEAD,
                "run_id": 100 + index,
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
                "event": "workflow_dispatch",
                "html_url": (
                    "https://github.com/Zennay/Haxlab/actions/runs/"
                    f"{100 + index}"
                ),
            }
            for index, (gate, (workflow_path, workflow_name)) in enumerate(
                REQUIRED_GATES.items()
            )
        ],
    }


def passed_decision() -> PromotionDecision:
    return PromotionDecision(
        promote=True,
        reasons=(
            "head_to_head_gate_passed",
            "frozen_scenarios_passed",
            "no_blocking_regressions",
            "run_reproducible",
        ),
    )


def rejected_decision() -> PromotionDecision:
    return PromotionDecision(
        promote=False,
        reasons=("critical_regressions:kickoff",),
    )


def authorize(**overrides):
    values = {
        "exact_head": HEAD,
        "candidate_id": "candidate-d",
        "champion_id": "champion-b",
        "promotion_decision": passed_decision(),
        "gate_receipt": gate_receipt(),
        "evidence_sha256": EVIDENCE_SHA,
        "policy_sha256": POLICY_SHA,
    }
    values.update(overrides)
    return authorize_promotion(**values)


def test_exact_green_gate_receipt_authorizes_passed_decision() -> None:
    result = authorize()

    assert result["schema"] == SCHEMA_VERSION
    assert result["scope"] == "promotion-authorization-only"
    assert result["authorized"] is True
    assert result["exact_head"] == HEAD
    assert result["candidate_id"] == "candidate-d"
    assert result["champion_id"] == "champion-b"
    assert result["reasons"] == [
        "promotion_decision_passed",
        "mandatory_exact_head_gates_verified",
    ]
    assert [row["gate"] for row in result["mandatory_gates"]] == sorted(
        REQUIRED_GATES
    )
    assert "merge_authorized" not in result
    assert "ready_for_merge" not in result


def test_rejected_decision_never_becomes_authorized() -> None:
    result = authorize(promotion_decision=rejected_decision())

    assert result["authorized"] is False
    assert result["reasons"] == [
        "promotion_decision_rejected",
        "critical_regressions:kickoff",
    ]


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("exact_head", "A" * 40, "exact lowercase 40-character"),
        ("exact_head", "abc", "exact lowercase 40-character"),
        ("candidate_id", "", "non-empty native string"),
        ("candidate_id", 123, "non-empty native string"),
        ("evidence_sha256", "b" * 63, "64-character SHA-256"),
        ("policy_sha256", "C" * 64, "64-character SHA-256"),
    ],
)
def test_rejects_malformed_top_level_identity(
    field: str,
    value: object,
    match: str,
) -> None:
    with pytest.raises(PromotionAuthorizationError, match=match):
        authorize(**{field: value})


def test_candidate_must_differ_from_champion() -> None:
    with pytest.raises(
        PromotionAuthorizationError,
        match="must differ from champion_id",
    ):
        authorize(candidate_id="same", champion_id="same")


def test_decision_must_use_native_boolean_and_tuple_reasons() -> None:
    malformed_promote = PromotionDecision(
        promote="true",  # type: ignore[arg-type]
        reasons=("passed",),
    )
    with pytest.raises(
        PromotionAuthorizationError,
        match="promote must be native boolean",
    ):
        authorize(promotion_decision=malformed_promote)

    malformed_reasons = PromotionDecision(
        promote=True,
        reasons=["passed"],  # type: ignore[arg-type]
    )
    with pytest.raises(
        PromotionAuthorizationError,
        match="reasons must be a tuple",
    ):
        authorize(promotion_decision=malformed_reasons)


def test_decision_reasons_are_nonempty_unique_native_strings() -> None:
    with pytest.raises(
        PromotionAuthorizationError,
        match="must not be empty",
    ):
        authorize(
            promotion_decision=PromotionDecision(promote=True, reasons=())
        )

    with pytest.raises(
        PromotionAuthorizationError,
        match="duplicate promotion_decision reason",
    ):
        authorize(
            promotion_decision=PromotionDecision(
                promote=True,
                reasons=("passed", "passed"),
            )
        )

    with pytest.raises(
        PromotionAuthorizationError,
        match=r"reasons\[0\].*native string",
    ):
        authorize(
            promotion_decision=PromotionDecision(
                promote=True,
                reasons=(1,),  # type: ignore[arg-type]
            )
        )


def test_gate_receipt_exact_head_must_match_authorized_head() -> None:
    receipt = gate_receipt()
    receipt["exact_head"] = "d" * 40

    with pytest.raises(
        PromotionAuthorizationError,
        match="exact head does not match",
    ):
        authorize(gate_receipt=receipt)


def test_truthy_green_flag_does_not_authorize() -> None:
    receipt = gate_receipt()
    receipt["mandatory_gates_green"] = "true"

    with pytest.raises(
        PromotionAuthorizationError,
        match="must be native true",
    ):
        authorize(gate_receipt=receipt)


def test_each_mandatory_gate_is_revalidated_not_just_green_flag() -> None:
    receipt = gate_receipt()
    receipt["gates"][0]["conclusion"] = "failure"

    with pytest.raises(
        PromotionAuthorizationError,
        match="terminal-success evidence",
    ):
        authorize(gate_receipt=receipt)


def test_gate_run_head_cannot_drift_from_authorized_head() -> None:
    receipt = gate_receipt()
    receipt["gates"][1]["head_sha"] = "d" * 40

    with pytest.raises(
        PromotionAuthorizationError,
        match="not bound to the promotion exact head",
    ):
        authorize(gate_receipt=receipt)


def test_gate_receipt_requires_exact_mandatory_set() -> None:
    missing = gate_receipt()
    missing["gates"].pop()

    with pytest.raises(
        PromotionAuthorizationError,
        match="missing mandatory gates",
    ):
        authorize(gate_receipt=missing)

    duplicate = gate_receipt()
    duplicate["gates"][-1] = dict(duplicate["gates"][0])

    with pytest.raises(
        PromotionAuthorizationError,
        match="duplicate mandatory gate",
    ):
        authorize(gate_receipt=duplicate)


def test_gate_workflow_path_is_part_of_authorization_boundary() -> None:
    receipt = gate_receipt()
    receipt["gates"][0]["workflow_path"] = ".github/workflows/other.yml"

    with pytest.raises(
        PromotionAuthorizationError,
        match="workflow path mismatch",
    ):
        authorize(gate_receipt=receipt)


def test_gate_workflow_name_event_and_url_are_pinned() -> None:
    receipt = gate_receipt()
    receipt["gates"][0]["workflow_name"] = "Wrong workflow"
    with pytest.raises(
        PromotionAuthorizationError,
        match="workflow name mismatch",
    ):
        authorize(gate_receipt=receipt)

    receipt = gate_receipt()
    receipt["gates"][0]["event"] = "schedule"
    with pytest.raises(
        PromotionAuthorizationError,
        match="unsupported event type",
    ):
        authorize(gate_receipt=receipt)

    receipt = gate_receipt()
    receipt["gates"][0]["html_url"] += "-forged"
    with pytest.raises(
        PromotionAuthorizationError,
        match="workflow run URL mismatch",
    ):
        authorize(gate_receipt=receipt)


def test_gate_run_ids_are_positive_native_and_unique() -> None:
    receipt = gate_receipt()
    receipt["gates"][0]["run_id"] = True
    with pytest.raises(
        PromotionAuthorizationError,
        match="positive native integer",
    ):
        authorize(gate_receipt=receipt)

    receipt = gate_receipt()
    receipt["gates"][1]["run_id"] = receipt["gates"][0]["run_id"]
    with pytest.raises(
        PromotionAuthorizationError,
        match="reused across mandatory gates",
    ):
        authorize(gate_receipt=receipt)


def test_receipt_schema_is_pinned() -> None:
    receipt = gate_receipt()
    receipt["schema"] = "future-or-unknown-schema"

    with pytest.raises(
        PromotionAuthorizationError,
        match="schema mismatch",
    ):
        authorize(gate_receipt=receipt)


def test_rendered_authorization_is_deterministic() -> None:
    result = authorize()

    first = render_authorization(result)
    second = render_authorization(json.loads(first))

    assert first == second
    assert first.endswith("\n")


def test_focused_workflow_is_read_only_self_hosted_and_exact_sha_bound() -> None:
    path = Path(
        ".github/workflows/promotion-authorization-contract-validation.yml"
    )
    text = path.read_text(encoding="utf-8")

    assert "permissions:\n  contents: read" in text
    assert "runs-on: [self-hosted, haxlab]" in text
    assert "timeout-minutes: 15" in text
    assert "ubuntu-latest" not in text
    assert "workflow_dispatch:" in text
    assert "github.event.pull_request.head.sha || inputs.ref" in text
    assert "clean: true" in text
    assert '[[ ! "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in text
    assert 'ACTUAL_SHA="$(git rev-parse HEAD)"' in text
    assert ".promotion-auth-venv/bin/pytest -q tests/test_promotion_authorization.py" in text


def request_payload() -> dict:
    return {
        "exact_head": HEAD,
        "candidate_id": "candidate-d",
        "champion_id": "champion-b",
        "promotion_decision": {
            "promote": True,
            "reasons": [
                "head_to_head_gate_passed",
                "frozen_scenarios_passed",
                "no_blocking_regressions",
                "run_reproducible",
            ],
        },
        "gate_receipt": gate_receipt(),
        "evidence_sha256": EVIDENCE_SHA,
        "policy_sha256": POLICY_SHA,
    }


def test_strict_json_request_loader_round_trips_authorization(tmp_path) -> None:
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(request_payload(), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = load_authorization_request(request)

    assert result["authorized"] is True
    assert result["exact_head"] == HEAD
    assert result["evidence_sha256"] == EVIDENCE_SHA
    assert result["policy_sha256"] == POLICY_SHA


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (
            lambda payload: payload.update({"unexpected": True}),
            "unexpected fields",
        ),
        (
            lambda payload: payload.pop("policy_sha256"),
            "missing fields",
        ),
        (
            lambda payload: payload.__setitem__(
                "promotion_decision", {"promote": "true", "reasons": ["pass"]}
            ),
            "promote must be native boolean",
        ),
        (
            lambda payload: payload.__setitem__(
                "promotion_decision", {"promote": True, "reasons": "pass"}
            ),
            "reasons must be a JSON array",
        ),
        (
            lambda payload: payload.__setitem__(
                "promotion_decision",
                {"promote": True, "reasons": ["pass"], "extra": 1},
            ),
            "unexpected fields",
        ),
    ],
)
def test_json_request_loader_rejects_ambiguous_shapes(
    tmp_path,
    mutator,
    match: str,
) -> None:
    payload = request_payload()
    mutator(payload)
    request = tmp_path / "request.json"
    request.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(PromotionAuthorizationError, match=match):
        load_authorization_request(request)


def test_cli_emits_canonical_authorization_json(tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text(json.dumps(request_payload()) + "\n", encoding="utf-8")

    assert main([str(request)]) == 0

    rendered = capsys.readouterr().out
    parsed = json.loads(rendered)
    assert parsed["authorized"] is True
    assert rendered == render_authorization(parsed)


def test_cli_rejects_invalid_json_without_partial_authorization(
    tmp_path,
    capsys,
) -> None:
    request = tmp_path / "request.json"
    request.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main([str(request)])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "promotion authorization rejected" in captured.err
