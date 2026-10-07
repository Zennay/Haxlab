from __future__ import annotations

import argparse
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
WRITE_PERMISSION_LINE = re.compile(
    r"(?m)^[ \t]+[A-Za-z0-9_-]+:[ \t]*write[ \t]*$"
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


def _validates_exact_sha_input(text: str) -> bool:
    if INPUT_REF_MARKER not in text:
        return False
    return bool(
        re.search(
            r"""\^\[0-9a-f\]\{40\}\$""",
            text,
        )
    )


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


def _has_exact_head_guard(text: str) -> bool:
    lines = text.splitlines()
    exact_input_validated = _validates_exact_sha_input(text)
    expected_vars = _source_bound_vars(
        lines,
        exact_input_validated=exact_input_validated,
    )

    # Direct source-bound equality assertion.
    for line in lines:
        if "git rev-parse HEAD" not in line or not _is_equality_assertion(line):
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

        if _is_equality_assertion(line):
            return True

        # Canonical CI-style guard: mismatch enters a branch that exits nonzero.
        if "if [" in line and "!=" in line and _block_exits_nonzero(lines, index):
            return True

    return False


def _top_level_contents_read_only(text: str) -> bool:
    lines = text.splitlines()
    try:
        start = lines.index("permissions:")
    except ValueError:
        return False

    values: dict[str, str] = {}
    for line in lines[start + 1 :]:
        if line and not line.startswith(" "):
            break
        match = re.fullmatch(r"  ([A-Za-z0-9_-]+):\s*([^#]+?)\s*", line)
        if match:
            values[match.group(1)] = match.group(2)

    return values.get("contents") == "read"


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
    write_permissions = sorted(
        {match.group(0).strip() for match in WRITE_PERMISSION_LINE.finditer(text)}
    )
    contents_read_only = _top_level_contents_read_only(text)

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

    finding_count = sum(len(report["findings"]) for report in reports)
    return {
        "schema": SCHEMA,
        "workflow_count": len(reports),
        "finding_count": finding_count,
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
