from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "haxlab-evaluation-gate-run-receipt-v1"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

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
_GATE_EVENTS: dict[str, frozenset[str]] = {
    "calibration": frozenset({"push", "workflow_dispatch"}),
    "haxlab_ci": frozenset({"push", "pull_request", "workflow_dispatch"}),
    "multisource": frozenset({"push", "workflow_dispatch"}),
}
_RUN_FIELDS = frozenset(
    {
        "repository_full_name",
        "gate",
        "run_id",
        "run_attempt",
        "workflow_path",
        "workflow_name",
        "head_sha",
        "status",
        "conclusion",
        "event",
        "html_url",
    }
)
_REQUEST_FIELDS = frozenset({"exact_head", "runs", "github_runs"})


class GateRunReceiptError(ValueError):
    """Raised when exact-head workflow evidence is incomplete or untrusted."""


def _native_nonempty_string(value: Any, field: str) -> str:
    if type(value) is not str or not value:
        raise GateRunReceiptError(f"{field} must be a non-empty native string")
    return value


def _exact_sha(value: Any, field: str = "exact_head") -> str:
    value = _native_nonempty_string(value, field)
    if not _SHA_RE.fullmatch(value):
        raise GateRunReceiptError(f"{field} must be an exact lowercase 40-character commit SHA")
    return value


def _positive_native_int(value: Any, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise GateRunReceiptError(f"{field} must be a positive native integer")
    return value


def normalize_github_workflow_run(
    raw: dict[str, Any],
    *,
    gate: str,
) -> dict[str, Any]:
    """Normalize one GitHub Actions workflow-run API payload.

    GitHub's payload contains many unrelated fields. This boundary selects only
    the receipt fields and derives repository identity from the nested canonical
    repository object instead of trusting a caller-provided flattened value.
    """

    if type(raw) is not dict:
        raise GateRunReceiptError("GitHub workflow run must be a native JSON object")
    if gate not in REQUIRED_GATES:
        raise GateRunReceiptError(f"unknown mandatory gate: {gate!r}")

    repository = raw.get("repository")
    if type(repository) is not dict:
        raise GateRunReceiptError("GitHub workflow run repository must be a native JSON object")

    return {
        "repository_full_name": repository.get("full_name"),
        "gate": gate,
        "run_id": raw.get("id"),
        "run_attempt": raw.get("run_attempt"),
        "workflow_path": raw.get("path"),
        "workflow_name": raw.get("name"),
        "head_sha": raw.get("head_sha"),
        "status": raw.get("status"),
        "conclusion": raw.get("conclusion"),
        "event": raw.get("event"),
        "html_url": raw.get("html_url"),
    }


def validate_github_gate_runs(
    raw_runs: dict[str, dict[str, Any]],
    *,
    exact_head: str,
) -> dict[str, Any]:
    """Normalize and validate the exact mandatory GitHub Actions gate set."""

    if type(raw_runs) is not dict:
        raise GateRunReceiptError("GitHub gate runs must be a native JSON object")

    supplied = set(raw_runs)
    required = set(REQUIRED_GATES)
    missing = sorted(required - supplied)
    unknown = sorted(supplied - required)
    if missing:
        raise GateRunReceiptError(
            "missing mandatory GitHub workflow runs: " + ", ".join(missing)
        )
    if unknown:
        raise GateRunReceiptError(
            "unknown GitHub workflow run gates: " + ", ".join(unknown)
        )

    normalized = [
        normalize_github_workflow_run(raw_runs[gate], gate=gate)
        for gate in sorted(REQUIRED_GATES)
    ]
    return validate_gate_runs(normalized, exact_head=exact_head)


def validate_gate_runs(
    runs: Sequence[Mapping[str, Any]],
    *,
    exact_head: str,
) -> dict[str, Any]:
    """Validate the mandatory Arena-v2 merge/promotion workflow evidence.

    This deliberately consumes already-fetched GitHub workflow metadata rather
    than performing network access itself. A receipt is only emitted when all
    mandatory gates are present exactly once and completed successfully on the
    same immutable commit SHA.
    """

    exact_head = _exact_sha(exact_head)
    if type(runs) is not list:
        raise GateRunReceiptError("runs must be a native JSON array")

    seen: dict[str, dict[str, Any]] = {}
    seen_run_ids: set[int] = set()
    for index, raw in enumerate(runs):
        if type(raw) is not dict:
            raise GateRunReceiptError(f"runs[{index}] must be a native JSON object")
        unexpected = sorted(set(raw) - _RUN_FIELDS)
        if unexpected:
            raise GateRunReceiptError(
                f"runs[{index}] has unsupported fields: {', '.join(unexpected)}"
            )

        repository_full_name = _native_nonempty_string(
            raw.get("repository_full_name"), f"runs[{index}].repository_full_name"
        )
        if repository_full_name != "Zennay/Haxlab":
            raise GateRunReceiptError(
                f"runs[{index}].repository_full_name must be canonical Zennay/Haxlab"
            )

        gate = _native_nonempty_string(raw.get("gate"), f"runs[{index}].gate")
        if gate not in REQUIRED_GATES:
            raise GateRunReceiptError(f"runs[{index}].gate is not a mandatory gate: {gate!r}")
        if gate in seen:
            raise GateRunReceiptError(f"duplicate workflow evidence for mandatory gate {gate!r}")

        expected_path, expected_name = REQUIRED_GATES[gate]
        workflow_path = _native_nonempty_string(
            raw.get("workflow_path"), f"runs[{index}].workflow_path"
        )
        if workflow_path != expected_path:
            raise GateRunReceiptError(
                f"{gate} workflow_path mismatch: expected {expected_path!r}, "
                f"got {workflow_path!r}"
            )
        workflow_name = _native_nonempty_string(
            raw.get("workflow_name"), f"runs[{index}].workflow_name"
        )
        if workflow_name != expected_name:
            raise GateRunReceiptError(
                f"{gate} workflow_name mismatch: expected {expected_name!r}, "
                f"got {workflow_name!r}"
            )

        head_sha = _exact_sha(raw.get("head_sha"), f"runs[{index}].head_sha")
        if head_sha != exact_head:
            raise GateRunReceiptError(
                f"{gate} is bound to {head_sha}, not required exact head {exact_head}"
            )

        status = _native_nonempty_string(raw.get("status"), f"runs[{index}].status")
        conclusion = _native_nonempty_string(
            raw.get("conclusion"), f"runs[{index}].conclusion"
        )
        if status != "completed":
            raise GateRunReceiptError(f"{gate} is not terminal: status={status!r}")
        if conclusion != "success":
            raise GateRunReceiptError(
                f"{gate} did not pass: conclusion={conclusion!r}"
            )

        event = _native_nonempty_string(raw.get("event"), f"runs[{index}].event")
        if event not in _GATE_EVENTS[gate]:
            allowed = ", ".join(sorted(_GATE_EVENTS[gate]))
            raise GateRunReceiptError(
                f"{gate} uses unsupported event type {event!r}; allowed: {allowed}"
            )
        run_id = _positive_native_int(raw.get("run_id"), f"runs[{index}].run_id")
        run_attempt = _positive_native_int(
            raw.get("run_attempt"), f"runs[{index}].run_attempt"
        )
        if run_id in seen_run_ids:
            raise GateRunReceiptError(f"workflow run id {run_id} is reused across gates")
        seen_run_ids.add(run_id)
        html_url = _native_nonempty_string(
            raw.get("html_url"), f"runs[{index}].html_url"
        )
        expected_url = f"https://github.com/Zennay/Haxlab/actions/runs/{run_id}"
        if html_url != expected_url:
            raise GateRunReceiptError(
                f"{gate} html_url mismatch: expected {expected_url!r}, got {html_url!r}"
            )

        seen[gate] = {
            "repository_full_name": repository_full_name,
            "gate": gate,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "workflow_path": workflow_path,
            "workflow_name": workflow_name,
            "head_sha": head_sha,
            "status": status,
            "conclusion": conclusion,
            "event": event,
            "html_url": html_url,
        }

    missing = sorted(set(REQUIRED_GATES) - set(seen))
    if missing:
        raise GateRunReceiptError(
            "missing mandatory workflow evidence: " + ", ".join(missing)
        )

    if len(seen) != len(REQUIRED_GATES):
        raise GateRunReceiptError("receipt must contain exactly the mandatory gate set")

    return {
        "schema": SCHEMA_VERSION,
        "exact_head": exact_head,
        "mandatory_gates_green": True,
        "gates": [seen[name] for name in sorted(seen)],
    }


def render_receipt(receipt: Mapping[str, Any]) -> str:
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"


def load_request(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GateRunReceiptError(f"unable to read request JSON: {exc}") from exc
    if type(payload) is not dict:
        raise GateRunReceiptError("request must be a native JSON object")
    unexpected = sorted(set(payload) - _REQUEST_FIELDS)
    if unexpected:
        raise GateRunReceiptError(
            "request has unsupported fields: " + ", ".join(unexpected)
        )
    return payload


def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if type(payload) is not dict:
        raise GateRunReceiptError("request must be a native JSON object")
    unexpected = sorted(set(payload) - _REQUEST_FIELDS)
    if unexpected:
        raise GateRunReceiptError(
            "request has unsupported fields: " + ", ".join(unexpected)
        )

    exact_head = payload.get("exact_head")
    has_runs = "runs" in payload
    has_github_runs = "github_runs" in payload
    if has_runs == has_github_runs:
        raise GateRunReceiptError(
            "request must contain exactly one of runs or github_runs"
        )

    if has_github_runs:
        return validate_github_gate_runs(
            payload["github_runs"],
            exact_head=exact_head,
        )
    return validate_gate_runs(payload["runs"], exact_head=exact_head)


def _write_receipt_atomic(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        temp_path.replace(path)
    except OSError:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate exact-head HaxLab Arena-v2 mandatory workflow evidence"
    )
    parser.add_argument("request", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    try:
        payload = load_request(args.request)
        receipt = validate_request(payload)
        rendered = render_receipt(receipt)
        if args.output is None:
            print(rendered, end="")
        else:
            _write_receipt_atomic(args.output, rendered)
    except (GateRunReceiptError, OSError) as exc:
        if args.output is not None:
            try:
                args.output.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                parser.exit(
                    2,
                    f"gate receipt rejected: {exc}; "
                    f"stale output cleanup failed: {cleanup_exc}\n",
                )
        parser.exit(2, f"gate receipt rejected: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
