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


def _qualname(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


class _AliasCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self.aliases[local] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for item in node.names:
            if item.name == "*":
                continue
            local = item.asname or item.name
            self.aliases[local] = f"{module}.{item.name}" if module else item.name


class _OptimizationSafetyVisitor(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str]) -> None:
        self.aliases = aliases
        self.violations: list[str] = []

    def _add(self, node: ast.AST, reason: str) -> None:
        self.violations.append(f"{getattr(node, 'lineno', 0)}:{reason}")

    def visit_Assert(self, node: ast.Assert) -> None:
        self._add(node, "optimized_out_assert")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id == "__debug__":
            self._add(node, "__debug___dependency")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.ctx, ast.Load)
            and _qualname(node, self.aliases) == "sys.flags.optimize"
        ):
            self._add(node, "python_optimization_flag_dependency")
        self.generic_visit(node)


def _optimization_violations(
    source: str,
    *,
    filename: str = "<contract>",
) -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _AliasCollector()
    aliases.visit(tree)
    visitor = _OptimizationSafetyVisitor(aliases.aliases)
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
        ),
        key=lambda path: path.as_posix(),
    )


def test_data_pipeline_auditors_are_optimization_safe() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline audit module"

    violations: list[str] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        for violation in _optimization_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        ):
            violations.append(f"{relative}:{violation}")

    assert violations == []


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("assert clean", "optimized_out_assert"),
        ("assert value == expected, 'evidence drift'", "optimized_out_assert"),
        (
            "if __debug__:\n    validate_expensive_contract()",
            "__debug___dependency",
        ),
        (
            "import sys\nif sys.flags.optimize == 0:\n    validate_evidence()",
            "python_optimization_flag_dependency",
        ),
        (
            "from sys import flags as runtime_flags\n"
            "if runtime_flags.optimize:\n"
            "    return_fast_path()",
            "python_optimization_flag_dependency",
        ),
    ],
)
def test_contract_rejects_optimization_dependent_validation(
    source: str,
    reason: str,
) -> None:
    violations = _optimization_violations(source)
    assert any(reason in violation for violation in violations), violations


def test_contract_allows_explicit_fail_closed_validation() -> None:
    source = """
class AuditError(ValueError):
    pass

def audit(value: object) -> None:
    if value != "expected":
        raise AuditError("evidence mismatch")
    if not validate(value):
        _fail("validation_failed")
"""
    assert _optimization_violations(source) == []
