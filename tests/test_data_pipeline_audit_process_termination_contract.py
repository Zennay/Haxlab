from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = (
    ROOT / "src" / "haxlab" / "ingestion",
    ROOT / "src" / "haxlab" / "learning",
    ROOT / "src" / "haxlab" / "runtime",
)
WORKFLOW_PATH = (
    ROOT / ".github" / "workflows" / "data-pipeline-audit-process-termination-proof.yml"
)

TERMINATION_CALLS = {
    "builtins.exit",
    "builtins.quit",
    "exit",
    "os._exit",
    "os.abort",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.kill",
    "os.killpg",
    "quit",
    "signal.pthread_kill",
    "signal.raise_signal",
    "sys.exit",
}
TERMINATION_EXCEPTIONS = {
    "SystemExit",
    "builtins.SystemExit",
    "KeyboardInterrupt",
    "builtins.KeyboardInterrupt",
}


def _audit_modules() -> list[Path]:
    return sorted(
        {
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        },
        key=lambda path: path.as_posix(),
    )


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _canonical_name(node.args[0], aliases)
            if owner:
                return f"{owner}.{node.args[1].value}"
    return None


def _simple_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return node.target.id, node.value
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in {"builtins", "os", "signal", "sys"}:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "os",
            "signal",
            "sys",
        }:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            value = _canonical_name(expression, aliases)
            if value in TERMINATION_CALLS or value in TERMINATION_EXCEPTIONS:
                if aliases.get(local) != value:
                    aliases[local] = value
                    changed = True
    return aliases


def _is_main_guard(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False

    left = test.left
    right = test.comparators[0]
    pairs = ((left, right), (right, left))
    return any(
        isinstance(name, ast.Name)
        and name.id == "__name__"
        and isinstance(value, ast.Constant)
        and value.value == "__main__"
        for name, value in pairs
    )


def _is_canonical_cli_exit(node: ast.Raise, aliases: dict[str, str]) -> bool:
    exc = node.exc
    if not isinstance(exc, ast.Call):
        return False
    if not isinstance(exc.func, ast.Name) or exc.func.id != "SystemExit":
        return False
    if len(exc.args) != 1 or exc.keywords:
        return False

    result = exc.args[0]
    return (
        isinstance(result, ast.Call)
        and _canonical_name(result.func, aliases) == "main"
        and not result.args
        and not result.keywords
    )


def _allowed_cli_raise_ids(tree: ast.Module, aliases: dict[str, str]) -> set[int]:
    allowed: set[int] = set()
    for statement in tree.body:
        if not isinstance(statement, ast.If) or not _is_main_guard(statement.test):
            continue
        for child in statement.body:
            if isinstance(child, ast.Raise) and _is_canonical_cli_exit(child, aliases):
                allowed.add(id(child))
    return allowed


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    allowed_cli_raises = _allowed_cli_raise_ids(tree, aliases)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in TERMINATION_CALLS:
                findings.append(
                    f"line {node.lineno}: host-process termination/control call: {target}"
                )

        if isinstance(node, ast.Raise) and id(node) not in allowed_cli_raises:
            exc = node.exc
            target = (
                _canonical_name(exc.func, aliases)
                if isinstance(exc, ast.Call)
                else _canonical_name(exc, aliases)
            )
            if target in TERMINATION_EXCEPTIONS:
                findings.append(
                    f"line {node.lineno}: host-process termination exception: {target}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_have_no_noncanonical_process_termination() -> None:
    modules = _audit_modules()
    assert modules, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(path.read_text(encoding="utf-8"), filename=relative)
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sys\ndef audit():\n    sys.exit(2)\n", "sys.exit"),
        ("from sys import exit as stop\ndef audit():\n    stop(2)\n", "sys.exit"),
        ("import sys\nstop = sys.exit\ndef audit():\n    stop(2)\n", "sys.exit"),
        ("import sys\nstop = getattr(sys, 'exit')\ndef audit():\n    stop(2)\n", "sys.exit"),
        ("import os\ndef audit():\n    os._exit(2)\n", "os._exit"),
        ("import os\ndef audit():\n    os.abort()\n", "os.abort"),
        ("from os import execv as replace\ndef audit():\n    replace('/bin/false', ['/bin/false'])\n", "os.execv"),
        ("import os, signal\ndef audit():\n    os.kill(os.getpid(), signal.SIGTERM)\n", "os.kill"),
        ("from builtins import exit as stop\ndef audit():\n    stop(2)\n", "builtins.exit"),
        ("import signal\ndef audit():\n    signal.raise_signal(signal.SIGTERM)\n", "signal.raise_signal"),
        ("def audit():\n    raise SystemExit(2)\n", "SystemExit"),
        ("from builtins import SystemExit as Stop\ndef audit():\n    raise Stop(2)\n", "builtins.SystemExit"),
        ("Stop: object = SystemExit\ndef audit():\n    raise Stop(2)\n", "SystemExit"),
        ("def audit():\n    raise KeyboardInterrupt()\n", "KeyboardInterrupt"),
    ],
)
def test_contract_rejects_process_termination_paths(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_only_canonical_top_level_cli_exit() -> None:
    source = """
def main() -> int:
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
"""
    assert scan_source(source) == []


@pytest.mark.parametrize(
    "source",
    [
        """
def main() -> int:
    raise SystemExit(2)
if __name__ == "__main__":
    main()
""",
        """
def main() -> int:
    return 0
if __name__ == "__main__":
    import sys
    sys.exit(main())
""",
        """
def main() -> int:
    return 0
if __name__ == "__main__":
    raise SystemExit(7)
""",
        """
from builtins import SystemExit as Stop
def main() -> int:
    return 0
if __name__ == "__main__":
    raise Stop(main())
""",
        """
def wrapper() -> None:
    if __name__ == "__main__":
        raise SystemExit(main())
""",
        """
def main() -> int:
    return 0
if __name__ != "__main__":
    raise SystemExit(main())
""",
    ],
)
def test_contract_rejects_noncanonical_cli_exit_shapes(source: str) -> None:
    assert scan_source(source), source


def test_exact_head_workflow_rejects_stale_push_concurrency() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert (
        "group: haxlab-data-pipeline-audit-process-termination-${{ github.sha }}"
        in workflow
    )
    assert "cancel-in-progress: true" in workflow
    assert 'git ls-remote origin "refs/heads/${GITHUB_REF_NAME}"' in workflow
    assert 'test "$remote_head" = "${{ github.sha }}"' in workflow
