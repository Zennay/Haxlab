from __future__ import annotations

import ast
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INGESTION_ROOT = ROOT / "src" / "haxlab" / "ingestion"

TRACKED_MODULES = {"builtins", "locale", "os", "signal", "sys"}
MUTABLE_ROOTS = {"os.environ", "sys.modules", "sys.path"}
FORBIDDEN_CALLS = {
    "locale.setlocale",
    "os.chdir",
    "os.fchdir",
    "os.putenv",
    "os.umask",
    "os.unsetenv",
    "signal.set_wakeup_fd",
    "signal.signal",
    "sys.setprofile",
    "sys.setrecursionlimit",
    "sys.setswitchinterval",
    "sys.settrace",
}
ROOT_CONTAINER_TYPES = {
    "os.environ": "dict",
    "sys.modules": "dict",
    "sys.path": "list",
}
MUTATING_METHODS = {
    "os.environ": {
        "__delitem__",
        "__setitem__",
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
    },
    "sys.modules": {
        "__delitem__",
        "__setitem__",
        "clear",
        "pop",
        "popitem",
        "setdefault",
        "update",
    },
    "sys.path": {
        "__delitem__",
        "__setitem__",
        "append",
        "clear",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "sort",
    },
}


def _ingestion_modules() -> list[Path]:
    return sorted(path for path in INGESTION_ROOT.rglob("*.py") if path.is_file())


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


def _simple_assignments(node: ast.AST) -> list[tuple[str, ast.AST]]:
    if isinstance(node, ast.Assign):
        return [
            (target.id, node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        ]
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return [(node.target.id, node.value)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.value)]
    return []


def _tracked_alias_target(value: str) -> bool:
    if value in MUTABLE_ROOTS or value in FORBIDDEN_CALLS:
        return True
    if any(value == f"{root}.{method}" for root, methods in MUTATING_METHODS.items() for method in methods):
        return True
    return value.startswith(
        ("builtins.", "dict.", "list.", "locale.", "os.", "signal.", "sys.")
    )


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {
        "delattr": "builtins.delattr",
        "getattr": "builtins.getattr",
        "setattr": "builtins.setattr",
    }

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
            for local, expression in _simple_assignments(node):
                value = _canonical_name(expression, aliases)
                if value and _tracked_alias_target(value) and aliases.get(local) != value:
                    aliases[local] = value
                    changed = True

    return aliases


def _target_root(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, (ast.Name, ast.Attribute)):
        return _canonical_name(node, aliases)
    if isinstance(node, ast.Subscript):
        return _target_root(node.value, aliases)
    return None


def _unbound_mutation_from_call(
    target: str | None,
    node: ast.Call,
    aliases: dict[str, str],
) -> str | None:
    if target is None or not node.args:
        return None
    root = _canonical_name(node.args[0], aliases)
    if root not in ROOT_CONTAINER_TYPES:
        return None
    container = ROOT_CONTAINER_TYPES[root]
    for method in MUTATING_METHODS[root]:
        if target in {f"{container}.{method}", f"builtins.{container}.{method}"}:
            return f"mutation:{root}.{method}"
    return None


def _mutation_from_call(target: str | None) -> str | None:
    if target is None:
        return None
    if target in FORBIDDEN_CALLS:
        return f"call:{target}"
    for root, methods in MUTATING_METHODS.items():
        prefix = f"{root}."
        if target.startswith(prefix) and target[len(prefix):] in methods:
            return f"mutation:{target}"
    return None


def _violations(source: str) -> list[str]:
    tree = ast.parse(source)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            mutation = _mutation_from_call(target)
            if mutation is None:
                mutation = _unbound_mutation_from_call(target, node, aliases)
            if mutation is not None:
                findings.append(f"line:{node.lineno}:{mutation}")
                continue

            if target in {"setattr", "builtins.setattr", "delattr", "builtins.delattr"} and len(node.args) >= 2:
                owner = _canonical_name(node.args[0], aliases)
                attribute = node.args[1]
                if (
                    owner in {"os", "sys"}
                    and isinstance(attribute, ast.Constant)
                    and isinstance(attribute.value, str)
                ):
                    guarded = {"os": {"environ"}, "sys": {"modules", "path"}}
                    if attribute.value in guarded[owner]:
                        findings.append(
                            f"line:{node.lineno}:mutation:{owner}.{attribute.value}"
                        )

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                root = _target_root(target, aliases)
                if root in MUTABLE_ROOTS:
                    findings.append(f"line:{node.lineno}:assignment:{root}")

        if isinstance(node, ast.Delete):
            for target in node.targets:
                root = _target_root(target, aliases)
                if root in MUTABLE_ROOTS:
                    findings.append(f"line:{node.lineno}:delete:{root}")

    return sorted(set(findings))


def _kind(finding: str) -> str:
    return finding.split(":", 2)[2]


def test_ingestion_modules_do_not_mutate_process_global_state() -> None:
    modules = _ingestion_modules()
    assert modules, "expected ingestion Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        findings = _violations(path.read_text(encoding="utf-8"))
        if findings:
            failures[str(path.relative_to(ROOT))] = findings

    assert failures == {}


def test_contract_rejects_process_global_state_mutation() -> None:
    source = textwrap.dedent(
        """
        import locale
        import os
        import signal as sig
        import sys

        def ingest():
            os.chdir("/tmp")
            os.environ["MODE"] = "unsafe"
            del os.environ["OLD"]
            os.environ.update({"X": "1"})
            sys.path.append("/tmp/plugin")
            sys.modules.pop("haxlab.plugin", None)
            sig.signal(sig.SIGTERM, lambda *_: None)
            locale.setlocale(locale.LC_ALL, "C")
            sys.settrace(lambda *_: None)
        """
    )

    kinds = {_kind(finding) for finding in _violations(source)}
    assert {
        "call:locale.setlocale",
        "call:os.chdir",
        "call:signal.signal",
        "call:sys.settrace",
        "assignment:os.environ",
        "delete:os.environ",
        "mutation:os.environ.update",
        "mutation:sys.modules.pop",
        "mutation:sys.path.append",
    } <= kinds


def test_contract_resolves_import_assignment_and_getattr_aliases() -> None:
    source = textwrap.dedent(
        """
        import builtins as bi
        import os
        import sys
        from signal import signal as install

        env = mirror = os.environ
        append_path = sys.path.append
        pop_module = getattr(sys.modules, "pop")
        set_path = bi.getattr(sys.path, "insert")
        update_mapping = dict.update
        append_list = list.append

        def ingest():
            env.clear()
            mirror["X"] = "1"
            append_path("/tmp")
            pop_module("haxlab.plugin", None)
            set_path(0, "/tmp")
            update_mapping(env, {"Y": "2"})
            append_list(sys.path, "/opt/plugin")
            install(2, lambda *_: None)
        """
    )

    kinds = {_kind(finding) for finding in _violations(source)}
    assert {
        "call:signal.signal",
        "assignment:os.environ",
        "mutation:os.environ.clear",
        "mutation:sys.modules.pop",
        "mutation:sys.path.append",
        "mutation:sys.path.insert",
    } <= kinds


def test_contract_rejects_rebinding_guarded_process_containers() -> None:
    source = textwrap.dedent(
        """
        import os
        import sys
        from builtins import setattr as assign

        def ingest():
            assign(sys, "path", [])
            setattr(sys, "modules", {})
            delattr(os, "environ")
        """
    )

    kinds = {_kind(finding) for finding in _violations(source)}
    assert {
        "mutation:os.environ",
        "mutation:sys.modules",
        "mutation:sys.path",
    } <= kinds


def test_contract_allows_read_only_process_state_access() -> None:
    source = textwrap.dedent(
        """
        import locale
        import os
        import signal
        import sys

        def ingest():
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
