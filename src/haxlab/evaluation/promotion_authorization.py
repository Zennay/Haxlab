from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from haxlab.evaluation.models import PromotionDecision


SCHEMA_VERSION = "haxlab-promotion-authorization-v1"
GATE_RECEIPT_SCHEMA = "haxlab-evaluation-gate-run-receipt-v1"
REQUIRED_GATES: dict[str, tuple[str, str]] = {
    "calibration": (
        ".github/workflows/closed-loop-arena-v2-calibration.yml",
        "Closed-Loop Arena v2 Frozen Policy Validation",
    ),
    "haxlab_ci": (".github/workflows/ci.yml", "HaxLab CI"),
    "multisource": (
        ".github/workflows/multisource-suite-v2.yml",
        "Freeze Multisource Evaluation Suite v2 Current-Main",
    ),
}
_ALLOWED_EVENTS = frozenset({"push", "pull_request", "workflow_dispatch"})
_REQUEST_FIELDS = frozenset(
    {
        "exact_head",
        "candidate_id",
        "champion_id",
        "promotion_decision",
        "gate_receipt",
        "evidence_sha256",
        "policy_sha256",
    }
)
_DECISION_FIELDS = frozenset({"promote", "reasons"})
_PROMOTION_PASS_REASONS = (
    "head_to_head_gate_passed",
    "frozen_scenarios_passed",
    "no_blocking_regressions",
    "run_reproducible",
)

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PromotionAuthorizationError(ValueError):
    """Raised when promotion authorization evidence is incomplete or untrusted."""


def _native_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise PromotionAuthorizationError(
            f"{field} must be a non-empty native string"
        )
    if value != value.strip():
        raise PromotionAuthorizationError(
            f"{field} must not contain leading or trailing whitespace"
        )
    return value


def _exact_commit_sha(value: Any, field: str) -> str:
    value = _native_nonempty_string(value, field)
    if not _SHA40_RE.fullmatch(value):
        raise PromotionAuthorizationError(
            f"{field} must be an exact lowercase 40-character commit SHA"
        )
    return value


def _sha256(value: Any, field: str) -> str:
    value = _native_nonempty_string(value, field)
    if not _SHA256_RE.fullmatch(value):
        raise PromotionAuthorizationError(
            f"{field} must be a lowercase 64-character SHA-256"
        )
    return value


def _positive_native_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise PromotionAuthorizationError(
            f"{field} must be a positive native integer"
        )
    return value


def _decision_reasons(decision: PromotionDecision) -> tuple[str, ...]:
    reasons = decision.reasons
    if type(reasons) is not tuple:
        raise PromotionAuthorizationError(
            "promotion_decision.reasons must be a tuple"
        )
    if not reasons:
        raise PromotionAuthorizationError(
            "promotion_decision.reasons must not be empty"
        )

    normalized: list[str] = []
    seen: set[str] = set()
    for index, reason in enumerate(reasons):
        reason = _native_nonempty_string(
            reason, f"promotion_decision.reasons[{index}]"
        )
        if reason in seen:
            raise PromotionAuthorizationError(
                f"duplicate promotion_decision reason: {reason!r}"
            )
        seen.add(reason)
        normalized.append(reason)
    return tuple(normalized)


def _validate_gate_receipt(
    receipt: Mapping[str, Any],
    *,
    exact_head: str,
) -> list[dict[str, Any]]:
    if type(receipt) is not dict:
        raise PromotionAuthorizationError(
            "gate_receipt must be a native object"
        )

    if receipt.get("schema") != GATE_RECEIPT_SCHEMA:
        raise PromotionAuthorizationError("gate_receipt.schema mismatch")

    receipt_head = _exact_commit_sha(
        receipt.get("exact_head"), "gate_receipt.exact_head"
    )
    if receipt_head != exact_head:
        raise PromotionAuthorizationError(
            "gate_receipt exact head does not match promotion exact head"
        )

    if receipt.get("mandatory_gates_green") is not True:
        raise PromotionAuthorizationError(
            "gate_receipt.mandatory_gates_green must be native true"
        )

    gates = receipt.get("gates")
    if type(gates) is not list:
        raise PromotionAuthorizationError(
            "gate_receipt.gates must be a native array"
        )

    seen: dict[str, dict[str, Any]] = {}
    seen_run_ids: set[int] = set()
    for index, raw in enumerate(gates):
        if type(raw) is not dict:
            raise PromotionAuthorizationError(
                f"gate_receipt.gates[{index}] must be a native object"
            )

        gate = _native_nonempty_string(
            raw.get("gate"), f"gate_receipt.gates[{index}].gate"
        )
        if gate not in REQUIRED_GATES:
            raise PromotionAuthorizationError(
                f"unexpected mandatory gate in receipt: {gate!r}"
            )
        if gate in seen:
            raise PromotionAuthorizationError(
                f"duplicate mandatory gate in receipt: {gate!r}"
            )

        expected_path, expected_name = REQUIRED_GATES[gate]
        workflow_path = _native_nonempty_string(
            raw.get("workflow_path"),
            f"gate_receipt.gates[{index}].workflow_path",
        )
        if workflow_path != expected_path:
            raise PromotionAuthorizationError(
                f"{gate} workflow path mismatch"
            )

        workflow_name = _native_nonempty_string(
            raw.get("workflow_name"),
            f"gate_receipt.gates[{index}].workflow_name",
        )
        if workflow_name != expected_name:
            raise PromotionAuthorizationError(
                f"{gate} workflow name mismatch"
            )

        gate_head = _exact_commit_sha(
            raw.get("head_sha"),
            f"gate_receipt.gates[{index}].head_sha",
        )
        if gate_head != exact_head:
            raise PromotionAuthorizationError(
                f"{gate} is not bound to the promotion exact head"
            )

        if raw.get("status") != "completed":
            raise PromotionAuthorizationError(
                f"{gate} is not terminal-success evidence"
            )
        if raw.get("conclusion") != "success":
            raise PromotionAuthorizationError(
                f"{gate} is not terminal-success evidence"
            )

        event = _native_nonempty_string(
            raw.get("event"), f"gate_receipt.gates[{index}].event"
        )
        if event not in _ALLOWED_EVENTS:
            raise PromotionAuthorizationError(
                f"{gate} uses unsupported event type {event!r}"
            )

        run_id = _positive_native_int(
            raw.get("run_id"), f"gate_receipt.gates[{index}].run_id"
        )
        run_attempt = _positive_native_int(
            raw.get("run_attempt"),
            f"gate_receipt.gates[{index}].run_attempt",
        )
        if run_id in seen_run_ids:
            raise PromotionAuthorizationError(
                f"workflow run id {run_id} is reused across mandatory gates"
            )
        seen_run_ids.add(run_id)

        html_url = _native_nonempty_string(
            raw.get("html_url"),
            f"gate_receipt.gates[{index}].html_url",
        )
        expected_url = f"https://github.com/Zennay/Haxlab/actions/runs/{run_id}"
        if html_url != expected_url:
            raise PromotionAuthorizationError(
                f"{gate} workflow run URL mismatch"
            )

        seen[gate] = {
            "gate": gate,
            "workflow_path": workflow_path,
            "workflow_name": workflow_name,
            "head_sha": gate_head,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "status": "completed",
            "conclusion": "success",
            "event": event,
            "html_url": html_url,
        }

    missing = sorted(set(REQUIRED_GATES) - set(seen))
    if missing:
        raise PromotionAuthorizationError(
            "missing mandatory gates: " + ", ".join(missing)
        )
    if len(seen) != len(REQUIRED_GATES):
        raise PromotionAuthorizationError(
            "gate receipt must contain exactly the mandatory gate set"
        )

    return [seen[name] for name in sorted(seen)]


def authorize_promotion(
    *,
    exact_head: str,
    candidate_id: str,
    champion_id: str,
    promotion_decision: PromotionDecision,
    gate_receipt: Mapping[str, Any],
    evidence_sha256: str,
    policy_sha256: str,
) -> dict[str, Any]:
    """Bind a promotion decision to the exact green mandatory-gate evidence.

    This is intentionally narrower than merge authorization. It does not prove
    that the candidate branch is rebased onto the latest main or that a later
    resync has not invalidated the evidence.
    """

    exact_head = _exact_commit_sha(exact_head, "exact_head")
    candidate_id = _native_nonempty_string(candidate_id, "candidate_id")
    champion_id = _native_nonempty_string(champion_id, "champion_id")
    if candidate_id == champion_id:
        raise PromotionAuthorizationError(
            "candidate_id must differ from champion_id"
        )

    evidence_sha256 = _sha256(evidence_sha256, "evidence_sha256")
    policy_sha256 = _sha256(policy_sha256, "policy_sha256")

    if not isinstance(promotion_decision, PromotionDecision):
        raise PromotionAuthorizationError(
            "promotion_decision must be PromotionDecision"
        )
    if type(promotion_decision.promote) is not bool:
        raise PromotionAuthorizationError(
            "promotion_decision.promote must be native boolean"
        )
    decision_reasons = _decision_reasons(promotion_decision)
    if promotion_decision.promote is True:
        if decision_reasons != _PROMOTION_PASS_REASONS:
            raise PromotionAuthorizationError(
                "promoting decision must use canonical pass reasons"
            )
    elif decision_reasons == _PROMOTION_PASS_REASONS:
        raise PromotionAuthorizationError(
            "rejected decision cannot carry canonical pass reasons"
        )

    gates = _validate_gate_receipt(gate_receipt, exact_head=exact_head)

    authorized = promotion_decision.promote is True
    reasons = (
        ("promotion_decision_passed", "mandatory_exact_head_gates_verified")
        if authorized
        else ("promotion_decision_rejected", *decision_reasons)
    )

    return {
        "schema": SCHEMA_VERSION,
        "scope": "promotion-authorization-only",
        "exact_head": exact_head,
        "candidate_id": candidate_id,
        "champion_id": champion_id,
        "evidence_sha256": evidence_sha256,
        "policy_sha256": policy_sha256,
        "authorized": authorized,
        "reasons": list(reasons),
        "mandatory_gates": gates,
    }


def render_authorization(payload: Mapping[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        + "\n"
    )


def load_authorization_request(path: Path) -> dict[str, Any]:
    """Load and validate one strict JSON authorization request."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PromotionAuthorizationError(
            f"unable to read authorization request: {exc}"
        ) from exc

    if type(payload) is not dict:
        raise PromotionAuthorizationError(
            "authorization request must be a JSON object"
        )
    fields = set(payload)
    missing = sorted(_REQUEST_FIELDS - fields)
    unexpected = sorted(fields - _REQUEST_FIELDS)
    if missing:
        raise PromotionAuthorizationError(
            "authorization request missing fields: " + ", ".join(missing)
        )
    if unexpected:
        raise PromotionAuthorizationError(
            "authorization request has unexpected fields: "
            + ", ".join(unexpected)
        )

    raw_decision = payload["promotion_decision"]
    if type(raw_decision) is not dict:
        raise PromotionAuthorizationError(
            "promotion_decision must be a JSON object"
        )
    decision_fields = set(raw_decision)
    missing_decision = sorted(_DECISION_FIELDS - decision_fields)
    unexpected_decision = sorted(decision_fields - _DECISION_FIELDS)
    if missing_decision:
        raise PromotionAuthorizationError(
            "promotion_decision missing fields: "
            + ", ".join(missing_decision)
        )
    if unexpected_decision:
        raise PromotionAuthorizationError(
            "promotion_decision has unexpected fields: "
            + ", ".join(unexpected_decision)
        )

    promote = raw_decision["promote"]
    if type(promote) is not bool:
        raise PromotionAuthorizationError(
            "promotion_decision.promote must be native boolean"
        )
    raw_reasons = raw_decision["reasons"]
    if type(raw_reasons) is not list:
        raise PromotionAuthorizationError(
            "promotion_decision.reasons must be a JSON array"
        )

    decision = PromotionDecision(
        promote=promote,
        reasons=tuple(raw_reasons),
    )
    return authorize_promotion(
        exact_head=payload["exact_head"],
        candidate_id=payload["candidate_id"],
        champion_id=payload["champion_id"],
        promotion_decision=decision,
        gate_receipt=payload["gate_receipt"],
        evidence_sha256=payload["evidence_sha256"],
        policy_sha256=payload["policy_sha256"],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Bind a HaxLab promotion decision to exact-head mandatory "
            "evaluation gate evidence"
        )
    )
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)

    try:
        authorization = load_authorization_request(args.request)
    except PromotionAuthorizationError as exc:
        parser.exit(2, f"promotion authorization rejected: {exc}\n")

    print(render_authorization(authorization), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
