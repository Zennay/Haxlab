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
TRACKED_NAMESPACES = {
    "sys",
    "sys.monitoring",
    "faulthandler",
    "tracemalloc",
    "threading",
}
FORBIDDEN_CALLS = {
    "sys.addaudithook",
    "faulthandler.enable",
    "faulthandler.disable",
    "faulthandler.register",
    "faulthandler.unregister",
    "faulthandler.dump_traceback_later",
    "faulthandler.cancel_dump_traceback_later",
    "tracemalloc.start",
    "tracemalloc.stop",
    "tracemalloc.reset_peak",
    "threading.settrace",
    "threading.setprofile",
    "threading.settrace_all_threads",
    "threading.setprofile_all_threads",
    "sys.monitoring.use_tool_id",
    "sys.monitoring.free_tool_id",
    "sys.monitoring.register_callback",
    "sys.monitoring.set_events",
    "sys.monitoring.set_local_events",
    "sys.monitoring.restart_events",
}
WILDCARD_MODULES = {
    "sys",
    "faulthandler",
    "tracemalloc",
    "threading",
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


def _tracked_alias(name: str | None) -> bool:
    if not name:
        return False
    if name in TRACKED_NAMESPACES or name in FORBIDDEN_CALLS:
        return True
    return any(name.startswith(f"{namespace}.") for namespace in TRACKED_NAMESPACES)


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = (
                    item.name if item.asname else item.name.split(".", 1)[0]
                )
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
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
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
            if (
                module in WILDCARD_MODULES
                and any(item.name == "*" for item in node.names)
            ):
                findings.append(
                    f"line {node.lineno}: wildcard instrumentation import: {module}"
                )
        elif isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: process instrumentation mutation: {target}"
                )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_mutate_process_instrumentation() -> None:
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
        ("import sys\nsys.addaudithook(lambda *args: None)\n", "sys.addaudithook"),
        (
            "from faulthandler import enable as turn_on\nturn_on()\n",
            "faulthandler.enable",
        ),
        (
            "import faulthandler as fh\nfh.dump_traceback_later(10)\n",
            "faulthandler.dump_traceback_later",
        ),
        (
            "import tracemalloc\nbegin = tracemalloc.start\nbegin()\n",
            "tracemalloc.start",
        ),
        (
            "import tracemalloc\ngetattr(tracemalloc, 'stop')()\n",
            "tracemalloc.stop",
        ),
        (
            "from threading import settrace as install_trace\n"
            "install_trace(lambda *args: None)\n",
            "threading.settrace",
        ),
        (
            "import threading as th\nth.setprofile_all_threads(lambda *args: None)\n",
            "threading.setprofile_all_threads",
        ),
        (
            "import sys\nmon = sys.monitoring\nmon.use_tool_id(1, 'audit')\n",
            "sys.monitoring.use_tool_id",
        ),
        (
            "import sys\nmon = getattr(sys, 'monitoring')\n"
            "set_events = getattr(mon, 'set_events')\n"
            "set_events(1, 0)\n",
            "sys.monitoring.set_events",
        ),
        (
            "from sys import monitoring as mon\n"
            "mon.register_callback(1, 2, lambda *args: None)\n",
            "sys.monitoring.register_callback",
        ),
        (
            "from tracemalloc import *\n",
            "wildcard instrumentation import",
        ),
    ],
)
def test_contract_rejects_process_instrumentation_mutation(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_read_only_instrumentation_inspection() -> None:
    source = """
import faulthandler
import sys
import threading
import tracemalloc

def audit() -> tuple[bool, tuple[int, int], object, object, int]:
    enabled = faulthandler.is_enabled()
    traced = tracemalloc.get_traced_memory()
    trace_hook = threading.gettrace()
    profile_hook = threading.getprofile()
    events = sys.monitoring.get_events(1)
    return enabled, traced, trace_hook, profile_hook, events
"""
    assert scan_source(source) == []
