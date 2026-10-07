from __future__ import annotations

import json
from pathlib import Path

import pytest

from haxlab.evaluation.gate_run_receipt import (
    GateRunReceiptError,
    REQUIRED_GATES,
    SCHEMA_VERSION,
    normalize_github_workflow_run,
    render_receipt,
    validate_gate_runs,
    validate_github_gate_runs,
    validate_request,
)

HEAD = "a" * 40


def valid_runs():
    return [
        {
            "repository_full_name": "Zennay/Haxlab",
            "gate": gate,
            "run_id": index + 100,
            "run_attempt": 1,
            "workflow_path": path,
            "workflow_name": REQUIRED_GATES[gate][1],
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
            "event": "workflow_dispatch",
            "html_url": f"https://github.com/Zennay/Haxlab/actions/runs/{index + 100}",
        }
        for index, (gate, (path, _name)) in enumerate(REQUIRED_GATES.items())
    ]


def test_accepts_exact_green_gate_set_and_normalizes_order():
    runs = list(reversed(valid_runs()))

    receipt = validate_gate_runs(runs, exact_head=HEAD)

    assert receipt["schema"] == SCHEMA_VERSION
    assert receipt["exact_head"] == HEAD
    assert receipt["mandatory_gates_green"] is True
    assert "ready_for_merge_or_promotion" not in receipt
    assert [row["gate"] for row in receipt["gates"]] == sorted(REQUIRED_GATES)


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "repository_full_name": "Other/Haxlab"}
            ),
            "must be canonical Zennay/Haxlab",
        ),
        (lambda rows: rows.__setitem__(0, {**rows[0], "head_sha": "b" * 40}), "not required exact head"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "status": "in_progress"}), "not terminal"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "conclusion": "failure"}), "did not pass"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "run_id": True}), "positive native integer"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "run_attempt": 0}), "positive native integer"),
        (lambda rows: rows.__setitem__(0, {**rows[0], "run_attempt": True}), "positive native integer"),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "workflow_path": ".github/workflows/other.yml"}
            ),
            "workflow_path mismatch",
        ),
        (
            lambda rows: rows.__setitem__(
                0, {**rows[0], "workflow_name": "Other workflow"}
            ),
            "workflow_name mismatch",
        ),
        (
            lambda rows: rows.__setitem__(0, {**rows[0], "event": "schedule"}),
            "unsupported event type",
        ),
        (
            lambda rows: rows.__setitem__(
                0,
                {
                    **rows[0],
                    "html_url": "https://github.com/Zennay/Haxlab/actions/runs/100-extra",
                },
            ),
            "html_url mismatch",
        ),
        (
            lambda rows: rows.__setitem__(
                0,
                {key: value for key, value in rows[0].items() if key != "html_url"},
            ),
            "html_url must be a non-empty native string",
        ),
    ],
)
def test_rejects_untrusted_gate_evidence(mutator, match):
    runs = valid_runs()
    mutator(runs)

    with pytest.raises(GateRunReceiptError, match=match):
        validate_gate_runs(runs, exact_head=HEAD)


def test_rejects_missing_duplicate_unknown_and_non_object_rows():
    with pytest.raises(GateRunReceiptError, match="missing mandatory"):
        validate_gate_runs(valid_runs()[:-1], exact_head=HEAD)

    duplicate = valid_runs()
    duplicate[-1] = dict(duplicate[0])
    with pytest.raises(GateRunReceiptError, match="duplicate workflow evidence"):
        validate_gate_runs(duplicate, exact_head=HEAD)

    unknown = valid_runs()
    unknown[0] = {**unknown[0], "gate": "optional"}
    with pytest.raises(GateRunReceiptError, match="not a mandatory gate"):
        validate_gate_runs(unknown, exact_head=HEAD)

    reused_id = valid_runs()
    reused_id[1] = {
        **reused_id[1],
        "run_id": reused_id[0]["run_id"],
        "html_url": reused_id[0]["html_url"],
    }
    with pytest.raises(GateRunReceiptError, match="reused across gates"):
        validate_gate_runs(reused_id, exact_head=HEAD)

    malformed = valid_runs()
    malformed[0] = "not-an-object"
    with pytest.raises(GateRunReceiptError, match="native JSON object"):
        validate_gate_runs(malformed, exact_head=HEAD)

    with pytest.raises(GateRunReceiptError, match="native JSON array"):
        validate_gate_runs(tuple(valid_runs()), exact_head=HEAD)

    extra = valid_runs()
    extra[0] = {**extra[0], "ignored_security_signal": "green"}
    with pytest.raises(GateRunReceiptError, match="unsupported fields"):
        validate_gate_runs(extra, exact_head=HEAD)


@pytest.mark.parametrize("bad_head", ["A" * 40, "abc", "-" * 40, True, None])
def test_rejects_mutable_or_malformed_exact_head(bad_head):
    with pytest.raises(GateRunReceiptError, match="exact_head"):
        validate_gate_runs(valid_runs(), exact_head=bad_head)


def test_receipt_serialization_is_deterministic():
    receipt = validate_gate_runs(valid_runs(), exact_head=HEAD)

    first = render_receipt(receipt)
    second = render_receipt(json.loads(first))

    assert first == second
    assert first.endswith("\n")


def test_cli_round_trip_writes_canonical_receipt(tmp_path):
    from haxlab.evaluation.gate_run_receipt import main

    request = tmp_path / "request.json"
    output = tmp_path / "receipt.json"
    request.write_text(
        json.dumps({"exact_head": HEAD, "runs": valid_runs()}),
        encoding="utf-8",
    )

    assert main([str(request), "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema"] == SCHEMA_VERSION
    assert payload["exact_head"] == HEAD
    assert payload["mandatory_gates_green"] is True
    assert [row["run_attempt"] for row in payload["gates"]] == [1, 1, 1]


def test_cli_rejects_malformed_request_without_receipt(tmp_path):
    from haxlab.evaluation.gate_run_receipt import main

    request = tmp_path / "request.json"
    output = tmp_path / "receipt.json"
    request.write_text("{not-json", encoding="utf-8")
    output.write_text('{"mandatory_gates_green":true}\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        main([str(request), "--output", str(output)])

    assert exc.value.code == 2
    assert not output.exists()


def test_cli_rejects_unknown_top_level_request_fields(tmp_path):
    from haxlab.evaluation.gate_run_receipt import main

    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "exact_head": HEAD,
                "runs": valid_runs(),
                "merge_authorized": True,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc:
        main([str(request)])

    assert exc.value.code == 2


def test_cli_success_replaces_stale_output_and_leaves_no_temp_file(tmp_path):
    from haxlab.evaluation.gate_run_receipt import main

    request = tmp_path / "request.json"
    output = tmp_path / "receipt.json"
    request.write_text(
        json.dumps({"exact_head": HEAD, "runs": valid_runs()}),
        encoding="utf-8",
    )
    output.write_text("stale\n", encoding="utf-8")

    assert main([str(request), "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mandatory_gates_green"] is True
    assert list(tmp_path.glob(".receipt.json.*.tmp")) == []


def github_raw_runs():
    raw = {}
    for row in valid_runs():
        raw[row["gate"]] = {
            "id": row["run_id"],
            "run_attempt": row["run_attempt"],
            "name": row["workflow_name"],
            "path": row["workflow_path"],
            "head_sha": row["head_sha"],
            "status": row["status"],
            "conclusion": row["conclusion"],
            "event": row["event"],
            "html_url": row["html_url"],
            "repository": {"full_name": row["repository_full_name"]},
            "irrelevant_api_field": {"nested": True},
        }
    return raw


def test_normalizes_realistic_github_run_payload_without_trusting_extra_fields():
    raw = github_raw_runs()["calibration"]

    normalized = normalize_github_workflow_run(raw, gate="calibration")

    assert normalized == valid_runs()[0]
    assert "irrelevant_api_field" not in normalized


def test_validates_complete_raw_github_gate_set():
    receipt = validate_github_gate_runs(github_raw_runs(), exact_head=HEAD)

    assert receipt["mandatory_gates_green"] is True
    assert {row["gate"] for row in receipt["gates"]} == set(REQUIRED_GATES)


def test_raw_github_gate_set_fails_closed_on_missing_unknown_or_wrong_repository():
    missing = github_raw_runs()
    missing.pop("multisource")
    with pytest.raises(GateRunReceiptError, match="missing mandatory GitHub"):
        validate_github_gate_runs(missing, exact_head=HEAD)

    unknown = github_raw_runs()
    unknown["optional"] = dict(unknown["calibration"])
    with pytest.raises(GateRunReceiptError, match="unknown GitHub workflow"):
        validate_github_gate_runs(unknown, exact_head=HEAD)

    wrong_repo = github_raw_runs()
    wrong_repo["calibration"] = {
        **wrong_repo["calibration"],
        "repository": {"full_name": "Other/Haxlab"},
    }
    with pytest.raises(GateRunReceiptError, match="canonical Zennay/Haxlab"):
        validate_github_gate_runs(wrong_repo, exact_head=HEAD)


def test_raw_github_gate_set_rejects_non_native_repository_shape():
    raw = github_raw_runs()["calibration"]
    raw["repository"] = "Zennay/Haxlab"

    with pytest.raises(GateRunReceiptError, match="repository must be a native JSON object"):
        normalize_github_workflow_run(raw, gate="calibration")


def test_receipt_validation_workflow_is_explicit_exact_head_and_self_hosted():
    workflow = Path(
        ".github/workflows/evaluation-gate-run-receipt-validation.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "expected_sha:" in workflow
    assert "required: true" in workflow
    assert "push:" in workflow
    assert "validation/evaluation-gate-run-receipt-20261007" in workflow
    assert "\n  pull_request:" not in workflow
    assert "startsWith(github.event.head_commit.message, '[receipt-proof]')" in workflow
    assert "runs-on: [self-hosted, haxlab]" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert '[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]' in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.expected_sha || github.sha" in workflow
    assert 'test "$GITHUB_SHA" = "$EXPECTED_SHA"' in workflow
    assert "test \"$(git rev-parse HEAD)\" = \"$GITHUB_SHA\"" in workflow
    assert "tests/test_evaluation_gate_run_receipt.py" in workflow


def test_cli_accepts_raw_github_gate_payloads(tmp_path):
    from haxlab.evaluation.gate_run_receipt import main

    request = tmp_path / "github-request.json"
    output = tmp_path / "github-receipt.json"
    request.write_text(
        json.dumps({"exact_head": HEAD, "github_runs": github_raw_runs()}),
        encoding="utf-8",
    )

    assert main([str(request), "--output", str(output)]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["mandatory_gates_green"] is True
    assert {row["gate"] for row in payload["gates"]} == set(REQUIRED_GATES)


def test_request_requires_exactly_one_input_representation():
    both = {
        "exact_head": HEAD,
        "runs": valid_runs(),
        "github_runs": github_raw_runs(),
    }
    with pytest.raises(GateRunReceiptError, match="exactly one"):
        validate_request(both)

    with pytest.raises(GateRunReceiptError, match="exactly one"):
        validate_request({"exact_head": HEAD})


def test_direct_request_validation_rejects_unknown_fields():
    with pytest.raises(GateRunReceiptError, match="unsupported fields"):
        validate_request(
            {
                "exact_head": HEAD,
                "runs": valid_runs(),
                "unvalidated_override": True,
            }
        )


def test_gate_event_contract_is_specific_per_workflow():
    calibration_pull_request = valid_runs()
    calibration_pull_request[0] = {
        **calibration_pull_request[0],
        "event": "pull_request",
    }
    with pytest.raises(GateRunReceiptError, match="calibration uses unsupported event"):
        validate_gate_runs(calibration_pull_request, exact_head=HEAD)

    ci_pull_request = valid_runs()
    ci_index = next(
        index for index, row in enumerate(ci_pull_request)
        if row["gate"] == "haxlab_ci"
    )
    ci_pull_request[ci_index] = {
        **ci_pull_request[ci_index],
        "event": "pull_request",
    }
    receipt = validate_gate_runs(ci_pull_request, exact_head=HEAD)
    assert next(row for row in receipt["gates"] if row["gate"] == "haxlab_ci")[
        "event"
    ] == "pull_request"
