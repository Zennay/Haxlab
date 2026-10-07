from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from haxlab.evaluation.models import PromotionDecision


SCHEMA_VERSION = "haxlab-promotion-authorization-v1"
GATE_RECEIPT_SCHEMA = "haxlab-evaluation-gate-run-receipt-v1"
REQUIRED_GATES: dict[str, str] = {
    "calibration": ".github/workflows/closed-loop-arena-v2-calibration.yml",
    "haxlab_ci": ".github/workflows/ci.yml",
    "multisource": ".github/workflows/multisource-suite-v2.yml",
}

_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class PromotionAuthorizationError(ValueError):
    """Raised when promotion authorization evidence is incomplete or untrusted."""


def _native_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise PromotionAuthorizationError(
            f"{field} must be a non-empty native string"
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
    if not isinstance(receipt, Mapping):
        raise PromotionAuthorizationError("gate_receipt must be an object")

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
    if isinstance(gates, (str, bytes)) or not isinstance(gates, Sequence):
        raise PromotionAuthorizationError("gate_receipt.gates must be a sequence")

    seen: dict[str, dict[str, Any]] = {}
    seen_run_ids: set[int] = set()
    for index, raw in enumerate(gates):
        if not isinstance(raw, Mapping):
            raise PromotionAuthorizationError(
                f"gate_receipt.gates[{index}] must be an object"
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

        workflow_path = _native_nonempty_string(
            raw.get("workflow_path"),
            f"gate_receipt.gates[{index}].workflow_path",
        )
        if workflow_path != REQUIRED_GATES[gate]:
            raise PromotionAuthorizationError(
                f"{gate} workflow path mismatch"
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

        seen[gate] = {
            "gate": gate,
            "workflow_path": workflow_path,
            "head_sha": gate_head,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "status": "completed",
            "conclusion": "success",
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
