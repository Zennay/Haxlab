from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


SCHEMA = "haxlab-evaluation-workflow-provenance-inventory-v1"
MAX_WORKFLOW_BYTES = 1_048_576
IMMUTABLE_ACTION_REF = re.compile(r"^[0-9a-f]{40}$")
USES_LINE = re.compile(r"""(?m)^[ \t]+uses:[ \t]*["']?([^"'\s#]+)["']?""")
RUNNER_LINE = re.compile(r"(?m)^\\s+runs-on:\\s*(.+?)\\s*$")
TIMEOUT_LINE = re.compile(r"(?m)^\\s+timeout-minutes:\\s*([0-9]+)\\s*$")
CONTINUE_ON_ERROR_TRUE = re.compile(
    r"""(?m)^\\s+continue-on-error:\\s*(?:true|"true"|'true')\\s*$"""
)
EVENT_SHA_MARKERS = (
    "github.sha",
    "github.event.pull_request.head.sha",
)
INPUT_REF_MARKER = "inputs.ref"

DEFAULT_WORKFLOWS: tuple[str, ...] = (
    ".github/workflows/arena-runner-config-integrity-validation.yml",
    ".github/workflows/arena-v2-evaluation-validation.yml",
    ".github/workflows/arena-v2-integration-validate.yml",
    ".github/workflows/arena-v2-metric-bounds-validation.yml",
    ".github/workflows/calibration-gate-contract-validation.yml",
    ".github/workflows/calibration-pointer-invariant-validation.yml",
    ".github/workflows/calibration-summary-pythonpath-validation.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/closed-loop-arena-v2-calibration.yml",
    ".github/workflows/closed-loop-native-numeric-validation.yml",
    ".github/workflows/duel-policy-integrity-validation.yml",
    ".github/workflows/elite-gate-preflight-integrity-validation.yml",
    ".github/workflows/evaluation-green-integration-validation.yml",
    ".github/workflows/multisource-suite-integrity-validation.yml",
    ".github/workflows/multisource-suite-v2.yml",
    ".github/workflows/promotion-evidence-validation.yml",
    ".github/workflows/promotion-policy-integrity-validation.yml",
    ".github/workflows/replay-scenario-state-integrity-validation.yml",
    ".github/workflows/runtime-model-integrity-validation.yml",
    ".github/workflows/scenario-source-integrity-validation.yml",
)


def _safe_relative_workflow_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"workflow path must be repository-relative: {value!r}")
    if path.suffix not in {".yml", ".yaml"}:
        raise ValueError(f"workflow path must be YAML: {value!r}")
    return path


def _read_regular_utf8(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise ValueError(f"cannot safely open workflow: {path}") from exc

    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"workflow is not a regular file: {path}")
        if metadata.st_size > MAX_WORKFLOW_BYTES:
            raise ValueError(
                f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes: {path}"
            )

        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = MAX_WORKFLOW_BYTES + 1 - total
            chunk = os.read(fd, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_WORKFLOW_BYTES:
                raise ValueError(
                    f"workflow exceeds {MAX_WORKFLOW_BYTES} bytes: {path}"
                )
    finally:
        os.close(fd)

    return b"".join(chunks).decode("utf-8", errors="strict")


def _action_tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in USES_LINE.finditer(text))


def _is_immutable_action(token: str) -> bool:
    if token.startswith("./"):
        return True
    if "@" not in token:
        return False
    _name, ref = token.rsplit("@", 1)
    return bool(IMMUTABLE_ACTION_REF.fullmatch(ref))


def _checkout_blocks(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if "uses:" not in line or "actions/checkout@" not in line:
            continue

        uses_indent = len(line) - len(line.lstrip(" "))
        step_indent = max(0, uses_indent - 2)
        start = index
        for candidate in range(index - 1, -1, -1):
            stripped = lines[candidate].lstrip(" ")
            indent = len(lines[candidate]) - len(stripped)
            if indent == step_indent and stripped.startswith("- "):
                start = candidate
                break

        end = len(lines)
        for candidate in range(index + 1, len(lines)):
            stripped = lines[candidate].lstrip(" ")
            indent = len(lines[candidate]) - len(stripped)
            if indent == step_indent and stripped.startswith("- "):
                end = candidate
                break

        blocks.append("\n".join(lines[start:end]))
    return tuple(blocks)


def _checkout_ref_kind(block: str, *, exact_input_validated: bool) -> str:
    ref_lines = [
        line.strip()
        for line in block.splitlines()
        if line.strip().startswith("ref:")
    ]
    if len(ref_lines) != 1:
        return "missing_or_ambiguous"
    ref_line = ref_lines[0]
    if any(marker in ref_line for marker in EVENT_SHA_MARKERS):
        return "event_source"
    if INPUT_REF_MARKER in ref_line:
        return "validated_input" if exact_input_validated else "mutable_input"
    match = re.fullmatch(
        r"""ref:\s*["']?([0-9a-f]{40})["']?\s*(?:#.*)?""",
        ref_line,
    )
    if match:
        return "immutable_commit"
    return "mutable_or_unbound"


def _source_bound_vars(
    lines: Sequence[str],
    *,
    exact_input_validated: bool,
) -> set[str]:
    variables: set[str] = set()
    for line in lines:
        has_event_sha = any(marker in line for marker in EVENT_SHA_MARKERS)
        has_validated_input = exact_input_validated and INPUT_REF_MARKER in line
        if not (has_event_sha or has_validated_input):
            continue
        match = re.match(
            r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*(?::|=)",
            line,
        )
        if match:
            variables.add(match.group(1))
    return variables


def _shell_var_on_line(name: str, line: str) -> bool:
    plain = "$" + name
    braced = "$" + "{" + name + "}"
    return plain in line or braced in line


def _is_equality_assertion(line: str) -> bool:
    if "test " not in line:
        return False
    if "!=" in line:
        return False
    return " = " in line or " == " in line


def _block_exits_nonzero(lines: Sequence[str], start: int) -> bool:
    for line in lines[start + 1 : start + 9]:
        stripped = line.strip()
        if stripped == "fi":
            return False
        match = re.fullmatch(r"exit\s+([0-9]+)", stripped)
        if match and int(match.group(1)) != 0:
            return True
    return False


def _input_ref_vars(lines: Sequence[str]) -> set[str]:
    variables: set[str] = set()
    for line in lines:
        if INPUT_REF_MARKER not in line:
            continue
        match = re.match(
            r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*(?::|=)",
            line,
        )
        if match:
            variables.add(match.group(1))
    return variables


def _validates_exact_sha_input(text: str) -> bool:
    lines = text.splitlines()
    input_vars = _input_ref_vars(lines)
    if not input_vars:
        return False

    literal_pattern = "^[0-9a-f]{40}$"
    for index, line in enumerate(lines):
        if literal_pattern not in line or "=~" not in line:
            continue
        if not any(_shell_var_on_line(name, line) for name in input_vars):
            continue

        # Fail-closed if an invalid value enters a branch that exits nonzero.
        if "if !" in line and _block_exits_nonzero(lines, index):
            return True

        # Or if a direct regex assertion explicitly exits nonzero on failure.
        if "||" in line:
            suffix = line.split("||", 1)[1].strip()
            match = re.match(r"exit\s+([0-9]+)", suffix)
            if match and int(match.group(1)) != 0:
                return True

    return False


def _has_errexit_before(lines: Sequence[str], index: int) -> bool:
    for line in reversed(lines[:index]):
        stripped = line.strip()
        if stripped.startswith("- name:"):
            return False
        if re.match(r"^set\s+-[A-Za-z]*e[A-Za-z]*", stripped):
            return True
    return False


def _line_exits_on_failure(line: str) -> bool:
    if "||" not in line:
        return False
    suffix = line.split("||", 1)[1].strip()
    match = re.match(r"exit\s+([0-9]+)", suffix)
    return bool(match and int(match.group(1)) != 0)


def _has_exact_head_guard(text: str) -> bool:
    lines = text.splitlines()
    exact_input_validated = _validates_exact_sha_input(text)
    expected_vars = _source_bound_vars(
        lines,
        exact_input_validated=exact_input_validated,
    )

    # Direct source-bound equality assertion.
    for index, line in enumerate(lines):
        if "git rev-parse HEAD" not in line or not _is_equality_assertion(line):
            continue
        if not (_has_errexit_before(lines, index) or _line_exits_on_failure(line)):
            continue
        has_event_sha = any(marker in line for marker in EVENT_SHA_MARKERS)
        has_validated_input = exact_input_validated and INPUT_REF_MARKER in line
        if has_event_sha or has_validated_input:
            return True
        if any(_shell_var_on_line(name, line) for name in expected_vars):
            return True

    actual_vars = {
        match.group(1)
        for match in re.finditer(
            r'(?m)^\s*([A-Z_][A-Z0-9_]*)="\$\(git rev-parse HEAD\)"\s*$',
            text,
        )
    }
    if not actual_vars or not expected_vars:
        return False

    for index, line in enumerate(lines):
        actual_present = any(_shell_var_on_line(name, line) for name in actual_vars)
        expected_present = any(
            _shell_var_on_line(name, line) for name in expected_vars
        )
        if not (actual_present and expected_present):
            continue

        if _is_equality_assertion(line) and (
            _has_errexit_before(lines, index) or _line_exits_on_failure(line)
        ):
            return True

        # Canonical CI-style guard: mismatch enters a branch that exits nonzero.
        if "if [" in line and "!=" in line and _block_exits_nonzero(lines, index):
            return True

    return False


def _permission_blocks(text: str) -> list[tuple[int, dict[str, str]]]:
    lines = text.splitlines()
    blocks: list[tuple[int, dict[str, str]]] = []
    for index, line in enumerate(lines):
        if line.strip() != "permissions:":
            continue
        base_indent = len(line) - len(line.lstrip(" "))
        values: dict[str, str] = {}
        for child in lines[index + 1 :]:
            if not child.strip():
                continue
            indent = len(child) - len(child.lstrip(" "))
            if indent <= base_indent:
                break
            if indent != base_indent + 2:
                continue
            match = re.fullmatch(
                r"\s*([A-Za-z0-9_-]+):\s*([^#]+?)\s*",
                child,
            )
            if match:
                values[match.group(1)] = match.group(2)
        blocks.append((base_indent, values))
    return blocks


def _top_level_contents_read_only(text: str) -> bool:
    for indent, values in _permission_blocks(text):
        if indent == 0:
            return values.get("contents") == "read"
    return False


def _write_permissions(text: str) -> list[str]:
    writes = {
        f"{name}: write"
        for _indent, values in _permission_blocks(text)
        for name, value in values.items()
        if value == "write"
    }
    return sorted(writes)


def _runner_is_haxlab_self_hosted(spec: str) -> bool:
    value = spec.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return False
    labels = {
        token.strip().strip('"').strip("'")
        for token in value[1:-1].split(",")
        if token.strip()
    }
    return labels == {"self-hosted", "haxlab"}


def audit_workflow_text(path: str, text: str) -> dict[str, object]:
    actions = _action_tokens(text)
    external_actions = tuple(token for token in actions if not token.startswith("./"))
    mutable_actions = sorted(
        {token for token in external_actions if not _is_immutable_action(token)}
    )

    checkout_blocks = _checkout_blocks(text)
    checkout_credentials_disabled = bool(checkout_blocks) and all(
        re.search(
            r"""(?m)^\s+persist-credentials:\s*(?:false|"false"|'false')\s*$""",
            block,
        )
        for block in checkout_blocks
    )
    checkout_clean = bool(checkout_blocks) and all(
        re.search(r"(?m)^\s+clean:\s*true\s*$", block)
        for block in checkout_blocks
    )
    exact_input_validated = _validates_exact_sha_input(text)
    checkout_ref_kinds = [
        _checkout_ref_kind(
            block,
            exact_input_validated=exact_input_validated,
        )
        for block in checkout_blocks
    ]
    checkout_refs_bound = bool(checkout_blocks) and all(
        kind in {"event_source", "validated_input", "immutable_commit"}
        for kind in checkout_ref_kinds
    )
    records_head = "git rev-parse HEAD" in text
    exact_head_guard = _has_exact_head_guard(text)
    write_permissions = _write_permissions(text)
    contents_read_only = _top_level_contents_read_only(text)
    runner_specs = RUNNER_LINE.findall(text)
    self_hosted_haxlab_only = bool(runner_specs) and all(
        _runner_is_haxlab_self_hosted(spec) for spec in runner_specs
    )
    timeout_minutes = [int(value) for value in TIMEOUT_LINE.findall(text)]
    bounded_timeouts = (
        bool(runner_specs)
        and len(timeout_minutes) == len(runner_specs)
        and all(1 <= value <= 480 for value in timeout_minutes)
    )
    continue_on_error_enabled = bool(CONTINUE_ON_ERROR_TRUE.search(text))

    findings: list[str] = []
    if mutable_actions:
        findings.append("mutable_action_refs")
    if checkout_blocks and not checkout_credentials_disabled:
        findings.append("checkout_persists_credentials")
    if checkout_blocks and not checkout_clean:
        findings.append("checkout_not_clean")
    if checkout_blocks and not checkout_refs_bound:
        findings.append("checkout_ref_unbound")
    if not exact_head_guard:
        findings.append("missing_exact_head_guard")
    if not contents_read_only:
        findings.append("top_level_contents_not_read_only")
    if write_permissions:
        findings.append("write_permissions_present")
    if not self_hosted_haxlab_only:
        findings.append("unexpected_runner")
    if not bounded_timeouts:
        findings.append("unbounded_or_invalid_timeout")
    if continue_on_error_enabled:
        findings.append("continue_on_error_enabled")

    return {
        "path": path,
        "actions": list(actions),
        "mutable_actions": mutable_actions,
        "checkout_count": len(checkout_blocks),
        "checkout_credentials_disabled": checkout_credentials_disabled,
        "checkout_clean": checkout_clean,
        "checkout_ref_kinds": checkout_ref_kinds,
        "checkout_refs_bound": checkout_refs_bound,
        "records_head": records_head,
        "has_exact_head_guard": exact_head_guard,
        "top_level_contents_read_only": contents_read_only,
        "write_permissions": write_permissions,
        "runner_specs": runner_specs,
        "self_hosted_haxlab_only": self_hosted_haxlab_only,
        "timeout_minutes": timeout_minutes,
        "bounded_timeouts": bounded_timeouts,
        "continue_on_error_enabled": continue_on_error_enabled,
        "findings": findings,
    }


def _missing_workflow_report(path: str) -> dict[str, object]:
    return {
        "path": path,
        "actions": [],
        "mutable_actions": [],
        "checkout_count": 0,
        "checkout_credentials_disabled": False,
        "checkout_clean": False,
        "checkout_ref_kinds": [],
        "checkout_refs_bound": False,
        "records_head": False,
        "has_exact_head_guard": False,
        "top_level_contents_read_only": False,
        "write_permissions": [],
        "runner_specs": [],
        "self_hosted_haxlab_only": False,
        "timeout_minutes": [],
        "bounded_timeouts": False,
        "continue_on_error_enabled": False,
        "findings": ["missing_workflow"],
    }


def build_inventory(
    repo_root: Path,
    workflows: Iterable[str] = DEFAULT_WORKFLOWS,
) -> dict[str, object]:
    requested = sorted(
        {_safe_relative_workflow_path(value).as_posix() for value in workflows}
    )
    reports: list[dict[str, object]] = []
    missing: list[str] = []

    for relative in requested:
        target = repo_root / relative
        try:
            text = _read_regular_utf8(target)
        except FileNotFoundError:
            missing.append(relative)
            reports.append(_missing_workflow_report(relative))
            continue
        reports.append(audit_workflow_text(relative, text))

    finding_totals = Counter(
        finding
        for report in reports
        for finding in report["findings"]
    )
    finding_count = sum(finding_totals.values())
    workflows_with_findings = sum(bool(report["findings"]) for report in reports)
    return {
        "schema": SCHEMA,
        "workflow_count": len(reports),
        "workflows_with_findings": workflows_with_findings,
        "finding_count": finding_count,
        "finding_totals": dict(sorted(finding_totals.items())),
        "missing_workflows": missing,
        "workflows": reports,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only provenance inventory for the canonical HaxLab "
            "evaluation-validation workflow set."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="repository root (default: current directory)",
    )
    parser.add_argument(
        "--workflow",
        action="append",
        dest="workflows",
        help="repository-relative workflow path; repeat to override the default set",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 2 when any provenance finding is reported",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workflows = tuple(args.workflows) if args.workflows else DEFAULT_WORKFLOWS
    report = build_inventory(args.repo_root, workflows)
    print(json.dumps(report, sort_keys=True, indent=2))
    if args.strict and report["finding_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
