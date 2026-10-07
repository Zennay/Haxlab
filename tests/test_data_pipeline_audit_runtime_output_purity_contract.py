from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "src" / "haxlab"

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
STDIO_FD_STREAMS = {"os.fdopen(1)", "os.fdopen(2)"}
DIRECT_OUTPUT_CALLS = {
    "builtins.print",
    "print",
    *(
        f"{stream}.{method}"
        for stream in STDIO_STREAMS | STDIO_FD_STREAMS
        for method in {"write", "writelines"}
    ),
}


def _audit_paths() -> list[Path]:
    return sorted(
        path
        for path in AUDIT_ROOT.rglob("*_audit.py")
        if path.is_file()
    )


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
        if accessor == "os.fdopen" and node.args:
            fd = node.args[0]
            if (
                isinstance(fd, ast.Constant)
                and isinstance(fd.value, int)
                and not isinstance(fd.value, bool)
                and fd.value in {1, 2}
            ):
                return f"os.fdopen({fd.value})"
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
                aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )

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
                or value in STDIO_FD_STREAMS
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

    def _visit_function_signature(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)

        for argument in [
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ]:
            if argument.annotation is not None:
                self.visit(argument.annotation)

        if node.args.vararg is not None and node.args.vararg.annotation is not None:
            self.visit(node.args.vararg.annotation)
        if node.args.kwarg is not None and node.args.kwarg.annotation is not None:
            self.visit(node.args.kwarg.annotation)

        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

        if node.returns is not None:
            self.visit(node.returns)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_signature(node)
        self.function_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_signature(node)
        self.function_stack.append(node.name)
        for statement in node.body:
            self.visit(statement)
        self.function_stack.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)
        self.function_stack.append("<lambda>")
        self.visit(node.body)
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
                        f"line {node.lineno}: runtime output outside main(): "
                        f"os.write({fd.value}, ...)"
                    )
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"write", "writelines"}
            ):
                owner = node.func.value
                if (
                    isinstance(owner, ast.Call)
                    and _canonical_name(owner.func, self.aliases) == "os.fdopen"
                    and owner.args
                ):
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


def test_data_pipeline_audit_libraries_are_silent_outside_cli_main() -> None:
    paths = _audit_paths()
    assert paths, "expected HaxLab data-pipeline *_audit.py modules"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT)
        findings = scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        )
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def audit():\n    print('noise')\n", "builtins.print"),
        (
            "from builtins import print as emit\ndef audit():\n    emit('noise')\n",
            "builtins.print",
        ),
        (
            "import sys\ndef audit():\n    sys.stderr.write('noise')\n",
            "sys.stderr.write",
        ),
        (
            "import sys\nout = sys.stdout\ndef audit():\n    out.write('noise')\n",
            "sys.stdout.write",
        ),
        (
            "import sys\ndef audit():\n    getattr(sys.stderr, 'write')('noise')\n",
            "sys.stderr.write",
        ),
        (
            "import sys\ndef audit():\n    sys.stdout.writelines(['noise'])\n",
            "sys.stdout.writelines",
        ),
        (
            "import sys\ndef audit():\n    sys.__stderr__.write('noise')\n",
            "sys.__stderr__.write",
        ),
        (
            "import os\ndef audit():\n    os.write(1, b'noise')\n",
            "os.write(1",
        ),
        (
            "from os import write\ndef audit():\n    write(2, b'noise')\n",
            "os.write(2",
        ),
        (
            "import os\ndef audit():\n    os.fdopen(1, 'w', closefd=False).write('noise')\n",
            "os.fdopen(1",
        ),
        (
            "import os\nout = os.fdopen(1, 'w', closefd=False)\n"
            "def audit():\n    out.write('noise')\n",
            "os.fdopen(1).write",
        ),
    ],
)
def test_contract_rejects_runtime_output_outside_main(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_rejects_method_nested_and_signature_output() -> None:
    class_method = """
class Auditor:
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
    nested_lambda = """
def main() -> int:
    emit = lambda: print("noise")
    emit()
    return 0
"""
    default_side_effect = """
def main(value: str = print("noise")) -> int:
    return 0
"""
    decorator_side_effect = """
def emit(fn):
    print("noise")
    return fn

@emit
def main() -> int:
    return 0
"""

    for source in (
        class_method,
        nested_helper,
        nested_lambda,
        default_side_effect,
        decorator_side_effect,
    ):
        assert any(
            "builtins.print" in finding
            for finding in scan_source(source)
        )


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


def test_contract_preserves_pure_returns_and_nonstdio_fd_writes() -> None:
    source = """
import os

def audit(value: int) -> str:
    os.write(3, b"audit")
    return str(value)
"""
    assert scan_source(source) == []
