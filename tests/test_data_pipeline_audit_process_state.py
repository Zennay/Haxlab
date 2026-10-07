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

FORBIDDEN_CALLS = {
    "os.chdir",
    "os.fchdir",
    "os.putenv",
    "os.unsetenv",
    "os.umask",
    "signal.signal",
    "signal.set_wakeup_fd",
    "locale.setlocale",
}
FORBIDDEN_MUTATING_METHODS = {
    "sys.path": {"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort"},
    "sys.modules": {"clear", "pop", "popitem", "setdefault", "update"},
    "os.environ": {"clear", "pop", "popitem", "setdefault", "update"},
}


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


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"os", "sys", "signal", "locale"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {"os", "sys", "signal", "locale"}:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    name = _name(node)
    if name is None:
        return None
    head, dot, tail = name.partition(".")
    if head in aliases:
        return aliases[head] + (dot + tail if dot else "")
    return name


def _target_root(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _canonical_name(node, aliases)
    if isinstance(node, ast.Subscript):
        return _target_root(node.value, aliases)
    return None


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_CALLS:
                findings.append(f"line:{node.lineno}:call:{target}")
                continue
            if isinstance(node.func, ast.Attribute):
                owner = _canonical_name(node.func.value, aliases)
                if owner in FORBIDDEN_MUTATING_METHODS and node.func.attr in FORBIDDEN_MUTATING_METHODS[owner]:
                    findings.append(f"line:{node.lineno}:mutation:{owner}.{node.func.attr}")

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                root = _target_root(target, aliases)
                if root in {"sys.path", "sys.modules", "os.environ"}:
                    findings.append(f"line:{node.lineno}:assignment:{root}")

        if isinstance(node, ast.Delete):
            for target in node.targets:
                root = _target_root(target, aliases)
                if root in {"sys.path", "sys.modules", "os.environ"}:
                    findings.append(f"line:{node.lineno}:delete:{root}")

    return sorted(set(findings))


def _finding_kind(finding: str) -> str:
    return finding.split(":", 2)[2]


def test_data_pipeline_auditors_do_not_mutate_process_global_state() -> None:
    modules = _audit_modules()
    assert modules, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in modules:
        findings = _violations(path.read_text(encoding="utf-8"))
        if findings:
            failures[str(path.relative_to(ROOT))] = findings

    assert failures == {}


def test_contract_rejects_process_global_state_mutation() -> None:
    source = textwrap.dedent(
        """
        import os
        import sys
        import signal as sig
        import locale

        def probe():
            os.chdir("/tmp")
            os.environ["MODE"] = "unsafe"
            del os.environ["OLD"]
            os.environ.update({"X": "1"})
            sys.path.append("/tmp/plugin")
            sys.modules.pop("haxlab.plugin", None)
            sig.signal(sig.SIGTERM, lambda *_: None)
            locale.setlocale(locale.LC_ALL, "C")
        """
    )

    kinds = {_finding_kind(finding) for finding in _violations(source)}
    assert {
        "call:os.chdir",
        "assignment:os.environ",
        "delete:os.environ",
        "mutation:os.environ.update",
        "mutation:sys.path.append",
        "mutation:sys.modules.pop",
        "call:signal.signal",
        "call:locale.setlocale",
    } <= kinds


def test_contract_resolves_direct_import_aliases() -> None:
    source = textwrap.dedent(
        """
        from os import chdir as cd, environ as env
        from signal import signal as install
        from sys import path as module_path

        def probe():
            cd("/tmp")
            env.clear()
            module_path.insert(0, "/tmp")
            install(2, lambda *_: None)
        """
    )

    kinds = {_finding_kind(finding) for finding in _violations(source)}
    assert "call:os.chdir" in kinds
    assert "mutation:os.environ.clear" in kinds
    assert "mutation:sys.path.insert" in kinds
    assert "call:signal.signal" in kinds


def test_contract_allows_read_only_process_state_access() -> None:
    source = textwrap.dedent(
        """
        import os
        import sys
        import signal
        import locale

        def probe():
            cwd = os.getcwd()
            mode = os.environ.get("MODE")
            paths = tuple(sys.path)
            loaded = "haxlab" in sys.modules
            current = signal.getsignal(signal.SIGTERM)
            encoding = locale.getpreferredencoding(False)
            return cwd, mode, paths, loaded, current, encoding
        """
    )

    assert _violations(source) == []
