from __future__ import annotations

import ast
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
AUDIT_ROOTS = tuple(
    SRC / "haxlab" / package
    for package in ("ingestion", "learning", "runtime")
)
BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}
SWALLOW_NODES = (ast.Pass, ast.Continue, ast.Break, ast.Return)


def _audit_modules() -> list[Path]:
    modules: list[Path] = []
    for root in AUDIT_ROOTS:
        if root.exists():
            modules.extend(sorted(root.rglob("*_audit.py")))
    return sorted(set(modules))


def _name(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "builtins":
                    aliases[alias.asname or "builtins"] = "builtins"
        elif isinstance(node, ast.ImportFrom) and node.module == "builtins":
            for alias in node.names:
                if alias.name in BROAD_EXCEPTION_NAMES:
                    aliases[alias.asname or alias.name] = alias.name
    return aliases


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    name = _name(node)
    if name is None:
        return None
    head, dot, tail = name.partition(".")
    if head in aliases:
        return aliases[head] + (dot + tail if dot else "")
    return name


def _is_broad_exception(
    node: ast.AST | None,
    aliases: dict[str, str],
) -> bool:
    if node is None:
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_broad_exception(item, aliases) for item in node.elts)
    name = _canonical_name(node, aliases)
    if name is None:
        return False
    return name.rsplit(".", 1)[-1] in BROAD_EXCEPTION_NAMES


def _is_fail_call(stmt: ast.stmt) -> bool:
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    target = _name(stmt.value.func)
    return target is not None and target.rsplit(".", 1)[-1] == "_fail"


def _suite_terminates(statements: list[ast.stmt]) -> bool:
    if not statements:
        return False
    final = statements[-1]
    if isinstance(final, ast.Raise):
        return True
    if _is_fail_call(final):
        return True
    if isinstance(final, ast.If):
        return (
            bool(final.body)
            and bool(final.orelse)
            and _suite_terminates(final.body)
            and _suite_terminates(final.orelse)
        )
    return False


def _handler_violations(
    handler: ast.ExceptHandler,
    aliases: dict[str, str],
) -> list[str]:
    if not _is_broad_exception(handler.type, aliases):
        return []

    violations: list[str] = []
    for node in ast.walk(handler):
        if isinstance(node, ast.Pass):
            violations.append("pass")
        elif isinstance(node, ast.Continue):
            violations.append("continue")
        elif isinstance(node, ast.Break):
            violations.append("break")
        elif isinstance(node, ast.Return):
            violations.append("return")

    if not _suite_terminates(handler.body):
        violations.append("fallthrough")

    return sorted(set(violations))


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _import_aliases(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for violation in _handler_violations(node, aliases):
            findings.append(f"line:{node.lineno}:{violation}")
    return sorted(findings)


def test_data_pipeline_auditors_do_not_swallow_broad_exceptions() -> None:
    modules = _audit_modules()
    assert modules, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in modules:
        findings = _violations(path.read_text(encoding="utf-8"))
        if findings:
            failures[str(path.relative_to(ROOT))] = findings

    assert failures == {}


def test_contract_accepts_fail_closed_broad_handlers() -> None:
    source = textwrap.dedent(
        """
        def reraises():
            try:
                work()
            except Exception:
                cleanup()
                raise

        def converts_to_audit_failure():
            try:
                work()
            except BaseException as exc:
                _fail(f"invalid: {exc}")

        def explicit_errors_remain_unrestricted():
            try:
                work()
            except ValueError:
                return None
        """
    )

    assert _violations(source) == []


def test_contract_rejects_broad_exception_swallowing() -> None:
    cases = {
        "bare_pass": """
            try:
                work()
            except:
                pass
        """,
        "exception_continue": """
            for item in items:
                try:
                    work(item)
                except Exception:
                    continue
        """,
        "base_return": """
            try:
                work()
            except BaseException:
                return True
        """,
        "tuple_fallthrough": """
            try:
                work()
            except (ValueError, Exception):
                log()
        """,
        "nested_swallow_before_raise": """
            try:
                work()
            except Exception:
                if optional:
                    pass
                raise
        """,
        "aliased_exception": """
            from builtins import Exception as Broad
            try:
                work()
            except Broad:
                pass
        """,
    }

    findings = {
        name: _violations("def probe():\n" + textwrap.indent(textwrap.dedent(source), "    "))
        for name, source in cases.items()
    }
    kinds = {
        name: sorted(finding.rsplit(":", 1)[-1] for finding in values)
        for name, values in findings.items()
    }

    assert kinds["bare_pass"] == ["fallthrough", "pass"]
    assert kinds["exception_continue"] == ["continue", "fallthrough"]
    assert kinds["base_return"] == ["fallthrough", "return"]
    assert kinds["tuple_fallthrough"] == ["fallthrough"]
    assert kinds["nested_swallow_before_raise"] == ["pass"]
    assert kinds["aliased_exception"] == ["fallthrough", "pass"]
