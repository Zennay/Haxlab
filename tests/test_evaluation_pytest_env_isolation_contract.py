from __future__ import annotations

from pathlib import Path
import re
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
EVALUATION_WORKFLOW_TOKENS = (
    "evaluation",
    "arena",
    "calibration",
    "multisource",
    "promotion",
    "duel",
    "scenario",
    "closed-loop",
)

VENV_CREATE_RE = re.compile(r"\bpython3\s+-m\s+venv\s+[^\s]+")
VENV_PYTEST_RE = re.compile(
    r"(?P<command>"
    r"(?:[^\s#]+/bin/pytest\b)"
    r"|(?:[^\s#]+/bin/python(?:3)?\b\s+-m\s+pytest\b)"
    r")"
)
SYSTEM_PYTEST_RE = re.compile(r"\bpython(?:3)?\b\s+-m\s+pytest\b")
BARE_PYTEST_RE = re.compile(
    r"^(?:(?:[A-Za-z_][A-Za-z0-9_]*=(?:\"[^\"]*\"|'[^']*'|[^\s]+))\s+)*"
    r"(?:sudo\s+)?pytest(?:\s|$)"
)


def _pytest_command(line: str) -> str | None:
    venv_match = VENV_PYTEST_RE.search(line)
    if venv_match is not None:
        return venv_match.group("command")

    system_match = SYSTEM_PYTEST_RE.search(line)
    if system_match is not None:
        return system_match.group(0)

    stripped = line.strip()
    bare_match = BARE_PYTEST_RE.match(stripped)
    if bare_match is not None:
        return "pytest"

    return None


def scan_workflow_text(source: str, *, filename: str = "<memory>") -> list[str]:
    findings: list[str] = []
    venv_lines = [
        index
        for index, line in enumerate(source.splitlines(), start=1)
        if VENV_CREATE_RE.search(line)
    ]

    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        command = _pytest_command(line)
        if command is None:
            continue

        if "/opt/haxlab/.venv/" in line:
            findings.append(
                f"{filename}:{line_number}: pytest depends on shared /opt/haxlab/.venv"
            )
            continue

        if command == "pytest":
            findings.append(
                f"{filename}:{line_number}: bare pytest is not bound to an isolated venv"
            )
            continue

        if command.startswith("python") and "/bin/python" not in command:
            findings.append(
                f"{filename}:{line_number}: system python -m pytest is not isolated"
            )
            continue

        if not any(create_line < line_number for create_line in venv_lines):
            findings.append(
                f"{filename}:{line_number}: pytest runs before an isolated venv is created"
            )

    return findings


def _is_evaluation_validation_workflow(path: Path) -> bool:
    name = path.name.lower()
    return any(token in name for token in EVALUATION_WORKFLOW_TOKENS)


def test_evaluation_validation_workflows_isolate_pytest_from_runner_venv() -> None:
    workflows = sorted(
        path
        for pattern in ("*.yml", "*.yaml")
        for path in WORKFLOW_ROOT.glob(pattern)
        if _is_evaluation_validation_workflow(path)
    )
    assert workflows, "expected evaluation-related workflows"

    failures: dict[str, list[str]] = {}
    pytest_workflows = 0
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        if "pytest" not in text:
            continue
        pytest_workflows += 1
        relative = path.relative_to(REPO_ROOT)
        findings = scan_workflow_text(text, filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert pytest_workflows, "expected at least one evaluation workflow that runs pytest"
    assert failures == {}


def test_scanner_accepts_repository_local_isolated_venv() -> None:
    source = textwrap.dedent(
        """
        jobs:
          validate:
            steps:
              - name: Create isolated test environment
                run: |
                  python3 -m venv .proof-venv
                  .proof-venv/bin/pip install -e '.[dev]'
              - name: Run focused tests
                run: |
                  PYTHONPATH="$GITHUB_WORKSPACE/src" .proof-venv/bin/pytest -q tests/test_gate.py
        """
    )

    assert scan_workflow_text(source) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            """
            run: |
              python3 -m venv .proof-venv
              /opt/haxlab/.venv/bin/pytest -q tests/test_gate.py
            """,
            "shared /opt/haxlab/.venv",
        ),
        (
            """
            run: |
              python3 -m venv .proof-venv
              pytest -q tests/test_gate.py
            """,
            "bare pytest",
        ),
        (
            """
            run: |
              python3 -m venv .proof-venv
              python3 -m pytest -q tests/test_gate.py
            """,
            "system python -m pytest",
        ),
        (
            """
            run: |
              .proof-venv/bin/pytest -q tests/test_gate.py
              python3 -m venv .proof-venv
            """,
            "before an isolated venv is created",
        ),
    ],
)
def test_scanner_rejects_nonisolated_pytest_evidence(source: str, expected: str) -> None:
    findings = "\n".join(scan_workflow_text(textwrap.dedent(source)))
    assert expected in findings


def test_scanner_accepts_venv_python_module_invocation() -> None:
    source = textwrap.dedent(
        """
        run: |
          python3 -m venv .proof-venv
          .proof-venv/bin/python -m pytest -q tests/test_gate.py
        """
    )

    assert scan_workflow_text(source) == []


def test_scanner_does_not_confuse_installing_pytest_with_running_it() -> None:
    source = textwrap.dedent(
        """
        run: |
          python3 -m venv .proof-venv
          .proof-venv/bin/python -m pip install --disable-pip-version-check pytest
          .proof-venv/bin/pytest -q tests/test_gate.py
        """
    )

    assert scan_workflow_text(source) == []
