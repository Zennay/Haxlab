from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "os.chdir",
    "os.fchdir",
    "os.putenv",
    "os.unsetenv",
    "os.umask",
    "signal.alarm",
    "signal.pthread_sigmask",
    "signal.setitimer",
    "signal.set_wakeup_fd",
    "signal.siginterrupt",
    "signal.signal",
    "locale.setlocale",
    "sys.set_asyncgen_hooks",
    "sys.set_coroutine_origin_tracking_depth",
    "sys.setprofile",
    "sys.setrecursionlimit",
    "sys.setswitchinterval",
    "sys.settrace",
}

FORBIDDEN_MUTATING_METHODS = {
    "sys.path": {
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
        "__setitem__",
        "__delitem__",
    },
    "sys.modules": {
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
        "__setitem__",
        "__delitem__",
    },
    "os.environ": {
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
        "__setitem__",
        "__delitem__",
    },
}

GUARDED_ATTRIBUTES = {
    "os": {"environ"},
    "sys": {"path", "modules"},
}


def _evaluation_modules() -> list[Path]:
    return sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())


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
                root = alias.name.split(".", 1)[0]
                if root in {"builtins", "locale", "os", "signal", "sys"}:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "locale",
            "os",
            "signal",
            "sys",
        }:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
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


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)

            if target in {"setattr", "delattr", "builtins.setattr", "builtins.delattr"} and len(node.args) >= 2:
                owner = _canonical_name(node.args[0], aliases)
                attribute = node.args[1]
                if (
                    owner in GUARDED_ATTRIBUTES
                    and isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                    and attribute.value in GUARDED_ATTRIBUTES[owner]
                ):
                    findings.append(
                        f"line {node.lineno}: process-state attribute mutation: "
                        f"{owner}.{attribute.value}"
                    )
                    continue

            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: process-state mutation call: {target}"
                )
                continue

            for owner, methods in FORBIDDEN_MUTATING_METHODS.items():
                prefix = f"{owner}."
                if target and target.startswith(prefix) and target[len(prefix):] in methods:
                    findings.append(
                        f"line {node.lineno}: process-state mutation method: {target}"
                    )
                    break

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                root = _target_root(target, aliases)
                if root in FORBIDDEN_MUTATING_METHODS:
                    findings.append(
                        f"line {node.lineno}: process-state assignment: {root}"
                    )

        if isinstance(node, ast.Delete):
            for target in node.targets:
                root = _target_root(target, aliases)
                if root in FORBIDDEN_MUTATING_METHODS:
                    findings.append(
                        f"line {node.lineno}: process-state deletion: {root}"
                    )

    return sorted(set(findings))


def test_evaluation_package_does_not_mutate_process_global_state() -> None:
    modules = _evaluation_modules()
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

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
            os.environ.__setitem__("Y", "2")
            setattr(sys, "path", ["/tmp/plugin"])
            sys.path.append("/tmp/plugin")
            sys.modules.pop("haxlab.plugin", None)
            sig.signal(sig.SIGTERM, lambda *_: None)
            sig.setitimer(sig.ITIMER_REAL, 1.0)
            locale.setlocale(locale.LC_ALL, "C")
            sys.settrace(lambda *args: None)
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "os.chdir",
        "os.environ",
        "sys.path",
        "sys.modules.pop",
        "signal.signal",
        "signal.setitimer",
        "locale.setlocale",
        "sys.settrace",
    ):
        assert expected in findings


def test_contract_resolves_direct_import_and_builtin_aliases() -> None:
    source = textwrap.dedent(
        """
        import sys as system
        from builtins import setattr as mutate_attr
        from os import chdir as cd, environ as env
        from signal import signal as install
        from sys import modules as loaded

        def probe():
            cd("/tmp")
            env.clear()
            loaded.__delitem__("haxlab.plugin")
            install(2, lambda *_: None)
            mutate_attr(system, "path", ["/tmp"])
        """
    )

    findings = "\n".join(scan_source(source))
    assert "os.chdir" in findings
    assert "os.environ.clear" in findings
    assert "sys.modules.__delitem__" in findings
    assert "signal.signal" in findings
    assert "sys.path" in findings


def test_contract_rejects_constant_getattr_mutation_bypasses() -> None:
    source = textwrap.dedent(
        """
        import os
        import sys
        from builtins import getattr as read_attr

        def probe():
            getattr(os, "chdir")("/tmp")
            getattr(os.environ, "update")({"MODE": "unsafe"})
            read_attr(sys, "settrace")(lambda *args: None)
            read_attr(sys, "path").append("/tmp/plugin")
        """
    )

    findings = "\n".join(scan_source(source))
    assert "os.chdir" in findings
    assert "os.environ.update" in findings
    assert "sys.settrace" in findings
    assert "sys.path.append" in findings


def test_contract_allows_read_only_process_state_access() -> None:
    source = textwrap.dedent(
        """
        import locale
        import os
        import signal
        import sys

        def probe():
            cwd = os.getcwd()
            mode = os.environ.get("MODE")
            paths = tuple(sys.path)
            loaded = "haxlab" in sys.modules
            current = signal.getsignal(signal.SIGTERM)
            encoding = locale.getpreferredencoding(False)
            recursion_limit = sys.getrecursionlimit()
            return cwd, mode, paths, loaded, current, encoding, recursion_limit
        """
    )

    assert scan_source(source) == []
