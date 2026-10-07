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
TRACKED_NAMESPACES = {"sys", "threading"}
FORBIDDEN_CALLS = {
    "sys.setrecursionlimit",
    "sys.setswitchinterval",
    "sys.set_int_max_str_digits",
}


def _audit_paths() -> list[Path]:
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
        owner = _canonical_name(node.value, aliases)
        return f"{owner}.{node.attr}" if owner else node.attr
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


def _assignment_pairs(node: ast.AST) -> list[tuple[str, ast.AST]]:
    if isinstance(node, ast.Assign):
        pairs: list[tuple[str, ast.AST]] = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                pairs.append((target.id, node.value))
            elif (
                isinstance(target, (ast.Tuple, ast.List))
                and isinstance(node.value, (ast.Tuple, ast.List))
                and len(target.elts) == len(node.value.elts)
            ):
                pairs.extend(
                    (left.id, right)
                    for left, right in zip(target.elts, node.value.elts, strict=True)
                    if isinstance(left, ast.Name)
                )
        return pairs
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return [(node.target.id, node.value)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.value)]
    return []


def _tracked_alias(name: str | None) -> bool:
    if not name:
        return False
    if name in TRACKED_NAMESPACES or name in FORBIDDEN_CALLS:
        return True
    if name == "threading.stack_size":
        return True
    return any(name.startswith(f"{namespace}.") for namespace in TRACKED_NAMESPACES)


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = item.name if item.asname else item.name.split(".", 1)[0]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                if item.name == "*":
                    continue
                local = item.asname or item.name
                aliases[local] = f"{module}.{item.name}" if module else item.name

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            for local, expression in _assignment_pairs(node):
                target = _canonical_name(expression, aliases)
                if _tracked_alias(target) and aliases.get(local) != target:
                    aliases[local] = target
                    changed = True
    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in TRACKED_NAMESPACES and any(
                item.name == "*" for item in node.names
            ):
                findings.append(
                    f"line {node.lineno}: wildcard interpreter-state import: {module}"
                )
        elif isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: interpreter tuning mutation: {target}"
                )
            elif target == "threading.stack_size" and (node.args or node.keywords):
                findings.append(
                    f"line {node.lineno}: interpreter tuning mutation: {target}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_mutate_interpreter_tuning_state() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(path.read_text(encoding="utf-8"), filename=relative)
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import sys\nsys.setrecursionlimit(2000)\n", "sys.setrecursionlimit"),
        (
            "from sys import setswitchinterval as tune\ntune(0.001)\n",
            "sys.setswitchinterval",
        ),
        (
            "import sys\nsetter = getattr(sys, 'set_int_max_str_digits')\nsetter(640)\n",
            "sys.set_int_max_str_digits",
        ),
        (
            "import threading as th\nstack = th.stack_size\nstack(262144)\n",
            "threading.stack_size",
        ),
        ("from sys import *\n", "wildcard interpreter-state import"),
        ("from threading import *\n", "wildcard interpreter-state import"),
    ],
)
def test_contract_rejects_interpreter_tuning_mutation(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_read_only_interpreter_tuning_inspection() -> None:
    source = """
import sys
import threading

def audit() -> tuple[int, float, int, int]:
    recursion = sys.getrecursionlimit()
    interval = sys.getswitchinterval()
    max_digits = sys.get_int_max_str_digits()
    stack = threading.stack_size()
    return recursion, interval, max_digits, stack
"""
    assert scan_source(source) == []
