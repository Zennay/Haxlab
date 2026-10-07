from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

TRACKED_MODULES = {"builtins", "os", "sys"}
STDIO_STREAMS = {
    "sys.stderr",
    "sys.stderr.buffer",
    "sys.stdout",
    "sys.stdout.buffer",
    "sys.__stderr__",
    "sys.__stderr__.buffer",
    "sys.__stdout__",
    "sys.__stdout__.buffer",
}
DIRECT_OUTPUT_CALLS = {
    "builtins.print",
    "print",
    *(f"{stream}.{method}" for stream in STDIO_STREAMS for method in {"write", "writelines"}),
}


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


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {"print": "builtins.print"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in TRACKED_MODULES:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in TRACKED_MODULES:
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
            if value and (
                value in DIRECT_OUTPUT_CALLS
                or value in STDIO_STREAMS
            ):
                if aliases.get(local) != value:
                    aliases[local] = value
                    changed = True
    return aliases


class OutputVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.function_stack: list[str] = []
        self.class_depth = 0
        self.findings: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_depth += 1
        self.generic_visit(node)
        self.class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def _inside_top_level_cli_main(self) -> bool:
        return self.class_depth == 0 and self.function_stack == ["main"]

    def visit_Call(self, node: ast.Call) -> None:
        target = _canonical_name(node.func, self.aliases)

        if not self._inside_top_level_cli_main():
            if target in DIRECT_OUTPUT_CALLS:
                self.findings.append(
                    f"line {node.lineno}: runtime output outside main(): {target}"
                )
            elif target == "os.write" and node.args:
                fd = node.args[0]
                if (
                    isinstance(fd, ast.Constant)
                    and isinstance(fd.value, int)
                    and not isinstance(fd.value, bool)
                    and fd.value in {1, 2}
                ):
                    self.findings.append(
                        f"line {node.lineno}: runtime output outside main(): os.write({fd.value}, ...)"
                    )
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {"write", "writelines"}:
                owner = node.func.value
                if isinstance(owner, ast.Call) and _canonical_name(owner.func, self.aliases) == "os.fdopen":
                    if owner.args:
                        fd = owner.args[0]
                        if (
                            isinstance(fd, ast.Constant)
                            and isinstance(fd.value, int)
                            and not isinstance(fd.value, bool)
                            and fd.value in {1, 2}
                        ):
                            self.findings.append(
                                f"line {node.lineno}: runtime output outside main(): "
                                f"os.fdopen({fd.value}, ...).{node.func.attr}"
                            )

        self.generic_visit(node)


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = OutputVisitor(_aliases(tree))
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_library_is_silent_outside_cli_main() -> None:
    modules = sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def gate():\n    print('noise')\n", "builtins.print"),
        (
            "from builtins import print as emit\ndef gate():\n    emit('noise')\n",
            "builtins.print",
        ),
        (
            "import sys\ndef gate():\n    sys.stderr.write('noise')\n",
            "sys.stderr.write",
        ),
        (
            "import sys\nout = sys.stdout\ndef gate():\n    out.write('noise')\n",
            "sys.stdout.write",
        ),
        (
            "import sys\ndef gate():\n    getattr(sys.stderr, 'write')('noise')\n",
            "sys.stderr.write",
        ),
        (
            "import sys\ndef gate():\n    sys.stdout.writelines(['noise'])\n",
            "sys.stdout.writelines",
        ),
        (
            "import sys\ndef gate():\n    sys.__stderr__.write('noise')\n",
            "sys.__stderr__.write",
        ),
        (
            "import os\ndef gate():\n    os.write(1, b'noise')\n",
            "os.write(1",
        ),
        (
            "from os import write\ndef gate():\n    write(2, b'noise')\n",
            "os.write(2",
        ),
        (
            "import os\ndef gate():\n    os.fdopen(1, 'w', closefd=False).write('noise')\n",
            "os.fdopen(1",
        ),
    ],
)
def test_contract_rejects_runtime_output_outside_main(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_rejects_method_or_nested_function_named_main() -> None:
    class_method = """
class Runner:
    def main(self) -> None:
        print("noise")
"""
    nested_helper = """
def main() -> int:
    def helper() -> None:
        print("noise")
    helper()
    return 0
"""
    assert any("builtins.print" in finding for finding in scan_source(class_method))
    assert any("builtins.print" in finding for finding in scan_source(nested_helper))


def test_contract_preserves_cli_output_in_main() -> None:
    source = """
import os
import sys

def main() -> int:
    print("machine-readable")
    sys.stderr.write("diagnostic\\n")
    os.write(1, b"ok\\n")
    return 0
"""
    assert scan_source(source) == []


def test_contract_preserves_pure_library_returns_and_nonstdio_fd_writes() -> None:
    source = """
import os

def gate(value: int) -> str:
    os.write(3, b"audit")
    return str(value)
"""
    assert scan_source(source) == []
