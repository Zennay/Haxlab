from __future__ import annotations

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"
BROAD_EXCEPTIONS = {"Exception", "BaseException", "builtins.Exception", "builtins.BaseException"}


class ExceptionBoundaryVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                self.aliases[local] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            if handler.type is None:
                self.violations.append(
                    f"line {handler.lineno}: bare except is forbidden in evaluation code"
                )
                continue

            broad = sorted(self._broad_exception_names(handler.type))
            if broad:
                self.violations.append(
                    f"line {handler.lineno}: broad exception handler is forbidden: {', '.join(broad)}"
                )

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        target = self._qualified_name(node.func)
        if target == "contextlib.suppress":
            broad: set[str] = set()
            for arg in node.args:
                broad.update(self._broad_exception_names(arg))
            if broad:
                self.violations.append(
                    f"line {node.lineno}: broad contextlib.suppress is forbidden: {', '.join(sorted(broad))}"
                )

        self.generic_visit(node)

    def _broad_exception_names(self, node: ast.expr) -> set[str]:
        if isinstance(node, ast.Tuple):
            found: set[str] = set()
            for item in node.elts:
                found.update(self._broad_exception_names(item))
            return found

        name = self._qualified_name(node)
        if name in BROAD_EXCEPTIONS:
            return {name}
        return set()

    def _qualified_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._qualified_name(node.value)
            if parent is None:
                return None
            return f"{parent}.{node.attr}"
        return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = ExceptionBoundaryVisitor()
    visitor.visit(tree)
    return visitor.violations


def test_evaluation_package_has_no_broad_exception_swallowing() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(path.read_text(encoding="utf-8"), filename=str(relative)):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("try:\n    work()\nexcept:\n    recover()\n", "bare except"),
        ("try:\n    work()\nexcept Exception:\n    recover()\n", "broad exception handler"),
        ("try:\n    work()\nexcept (ValueError, BaseException):\n    recover()\n", "broad exception handler"),
        ("import builtins as b\ntry:\n    work()\nexcept b.Exception:\n    recover()\n", "broad exception handler"),
        ("from builtins import Exception as Failure\ntry:\n    work()\nexcept Failure:\n    recover()\n", "broad exception handler"),
        ("import contextlib\nwith contextlib.suppress(Exception):\n    work()\n", "broad contextlib.suppress"),
        ("from contextlib import suppress as silence\nwith silence(BaseException):\n    work()\n", "broad contextlib.suppress"),
    ],
)
def test_detector_rejects_broad_exception_boundaries(source: str, expected: str) -> None:
    violations = scan_source(source)
    assert any(expected in violation for violation in violations), violations


@pytest.mark.parametrize(
    "source",
    [
        "try:\n    parse()\nexcept ValueError:\n    reject()\n",
        "try:\n    read()\nexcept (OSError, UnicodeError):\n    reject()\n",
        "import contextlib\nwith contextlib.suppress(FileNotFoundError):\n    cleanup()\n",
        "from contextlib import suppress\nwith suppress(OSError, ValueError):\n    cleanup()\n",
    ],
)
def test_detector_allows_explicit_expected_failures(source: str) -> None:
    assert scan_source(source) == []
