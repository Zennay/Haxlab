from __future__ import annotations

"""Protect data-pipeline evidence auditors from modifying host Python builtins."""

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = tuple(
    ROOT / "src" / "haxlab" / package
    for package in ("ingestion", "learning", "runtime", "skill")
)
_MAPPING_MUTATORS = {
    "__delitem__", "__ior__", "__setitem__", "clear", "pop", "popitem",
    "setdefault", "update",
}
_OPERATOR_MUTATORS = {"operator.setitem", "operator.delitem", "operator.ior"}
_DICT_MUTATORS = {
    f"{owner}.{method}"
    for owner in ("dict", "builtins.dict")
    for method in _MAPPING_MUTATORS
}


def _canonical(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        if node.id == "__builtins__":
            return "builtins"
        if node.id in {"builtins", "operator"} and node.id not in aliases:
            # A local mapping named 'builtins' is not the interpreter module.
            return None
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        root = _canonical(node.value, aliases)
        return f"{root}.{node.attr}" if root else None
    if isinstance(node, ast.Call):
        fn = _canonical(node.func, aliases)
        if fn in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
            owner = _canonical(node.args[0], aliases)
            member = node.args[1]
            if owner and isinstance(member, ast.Constant) and isinstance(member.value, str):
                return f"{owner}.{member.value}"
        if fn in {"vars", "builtins.vars"} and len(node.args) == 1:
            owner = _canonical(node.args[0], aliases)
            if owner == "builtins":
                return "builtins.__dict__"
    return None


def _bind(target: ast.AST, value: ast.AST, aliases: dict[str, str]) -> None:
    if isinstance(target, ast.Name):
        name = _canonical(value, aliases)
        if name and (name == "builtins" or name.startswith("builtins.") or name.startswith("operator.")):
            aliases[target.id] = name
        else:
            aliases.pop(target.id, None)
    elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
        if len(target.elts) == len(value.elts):
            for lhs, rhs in zip(target.elts, value.elts, strict=True):
                _bind(lhs, rhs, aliases)


def _dangerous_store(target: ast.AST, aliases: dict[str, str]) -> bool:
    if isinstance(target, ast.Attribute):
        owner = _canonical(target.value, aliases)
        return owner == "builtins" or owner == "builtins.__dict__"
    if isinstance(target, ast.Subscript):
        owner = _canonical(target.value, aliases)
        return owner == "builtins" or owner == "builtins.__dict__"
    if isinstance(target, (ast.List, ast.Tuple)):
        return any(_dangerous_store(item, aliases) for item in target.elts)
    return False


class _BuiltinStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {"__builtins__": "builtins"}
        self.findings: list[str] = []

    def _reject(self, node: ast.AST, reason: str) -> None:
        self.findings.append(f"{getattr(node, 'lineno', 0)}:{reason}")

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            self.aliases[local] = item.name if item.asname else item.name.split(".", 1)[0]

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for item in node.names:
            if item.name == "*":
                if module == "builtins":
                    self._reject(node, "wildcard_builtin_import")
                continue
            self.aliases[item.asname or item.name] = f"{module}.{item.name}"

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            if _dangerous_store(target, self.aliases):
                self._reject(node, "builtin_binding_write")
            _bind(target, node.value, self.aliases)
        # Targets are visited only to scan nested calls (for example vars()).
        for target in node.targets:
            self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            if _dangerous_store(node.target, self.aliases):
                self._reject(node, "builtin_binding_write")
            _bind(node.target, node.value, self.aliases)
        self.visit(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        if _dangerous_store(node.target, self.aliases):
            self._reject(node, "builtin_binding_write")
        _bind(node.target, node.value, self.aliases)
        self.visit(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        if _dangerous_store(node.target, self.aliases):
            self._reject(node, "builtin_binding_augwrite")
        self.visit(node.value)
        self.visit(node.target)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            if _dangerous_store(target, self.aliases):
                self._reject(node, "builtin_binding_delete")
            self.visit(target)

    def visit_Call(self, node: ast.Call) -> None:
        fn = _canonical(node.func, self.aliases)
        if fn in {"setattr", "builtins.setattr", "delattr", "builtins.delattr"}:
            if node.args and _canonical(node.args[0], self.aliases) in {"builtins", "builtins.__dict__"}:
                self._reject(node, "builtin_reflective_write")
        if fn and fn.startswith("builtins.__dict__."):
            if fn.rsplit(".", 1)[1] in _MAPPING_MUTATORS:
                self._reject(node, "builtin_mapping_mutator")
        if fn in {"builtins.__setattr__", "builtins.__delattr__"}:
            self._reject(node, "builtin_module_mutator")
        if fn in _DICT_MUTATORS and node.args:
            if _canonical(node.args[0], self.aliases) in {"builtins", "builtins.__dict__"}:
                self._reject(node, "builtin_unbound_mapping_mutator")
        if fn in _OPERATOR_MUTATORS and node.args:
            if _canonical(node.args[0], self.aliases) in {"builtins", "builtins.__dict__"}:
                self._reject(node, "builtin_operator_write")
        self.generic_visit(node)


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    visitor = _BuiltinStateVisitor()
    visitor.visit(ast.parse(source, filename=filename))
    return sorted(set(visitor.findings))


def test_all_data_pipeline_auditors_preserve_builtin_namespace() -> None:
    paths = sorted({
        path
        for root in AUDIT_ROOTS
        if root.exists()
        for path in root.rglob("*_audit.py")
        if path.is_file()
    })
    assert paths, "no data-pipeline audit modules found"
    assert any(path.parent.name == "skill" for path in paths), "skill auditor missing"
    failures = {
        path.relative_to(ROOT).as_posix(): scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(path.relative_to(ROOT)),
        )
        for path in paths
    }
    assert {path: findings for path, findings in failures.items() if findings} == {}


@pytest.mark.parametrize(("code", "reason"), [
    ("import builtins\nbuiltins.open = None", "builtin_binding_write"),
    ("import builtins as b\nb.__import__ = None", "builtin_binding_write"),
    ("from builtins import __dict__ as namespace\nnamespace['open'] = 1", "builtin_binding_write"),
    ("__builtins__['open'] = 1", "builtin_binding_write"),
    ("import builtins\nbuiltins.__dict__['open'] = 1", "builtin_binding_write"),
    ("import builtins\nbuiltins.__dict__['open'] += 1", "builtin_binding_augwrite"),
    ("import builtins\ndel builtins.open", "builtin_binding_delete"),
    ("import builtins\ndel builtins.__dict__['open']", "builtin_binding_delete"),
    ("import builtins\nsetattr(builtins, 'open', 1)", "builtin_reflective_write"),
    ("import builtins\ndelattr(builtins, 'open')", "builtin_reflective_write"),
    ("import builtins\nbuiltins.__dict__.update({'open': 1})", "builtin_mapping_mutator"),
    ("import builtins\nbuiltins.__dict__.clear()", "builtin_mapping_mutator"),
    ("import builtins\nns = vars(builtins)\nns.setdefault('open', 1)", "builtin_mapping_mutator"),
    ("import builtins as b\nnamespace = b.__dict__\nnamespace.pop('open')", "builtin_mapping_mutator"),
    ("from builtins import getattr as ga\nimport builtins\nns = ga(builtins, '__dict__')\nns.update({})", "builtin_mapping_mutator"),
    ("import builtins\nmethod = builtins.__dict__.update\nmethod({})", "builtin_mapping_mutator"),
    ("import builtins\nbuiltins.__dict__ |= {'open': 1}", "builtin_binding_augwrite"),
    ("import operator as op\nimport builtins\nop.setitem(builtins.__dict__, 'open', 1)", "builtin_operator_write"),
    ("from operator import delitem\nimport builtins\ndelitem(vars(builtins), 'open')", "builtin_operator_write"),
    ("import builtins\nbuiltins.__setattr__('open', None)", "builtin_module_mutator"),
    ("import builtins\ndict.update(builtins.__dict__, {'open': 1})", "builtin_unbound_mapping_mutator"),
    ("import builtins\ndict.__setitem__(builtins.__dict__, 'open', None)", "builtin_unbound_mapping_mutator"),
    ("from builtins import dict as dt\nimport builtins\ndt.pop(builtins.__dict__, 'open')", "builtin_unbound_mapping_mutator"),
    ("from builtins import *", "wildcard_builtin_import"),
])
def test_detects_process_builtin_mutation(code: str, reason: str) -> None:
    assert any(reason in x for x in scan_source(code)), code


@pytest.mark.parametrize("code", [
    "import builtins\nvalue = builtins.open",
    "import builtins\nvalue = builtins.__dict__.get('open')",
    "from builtins import getattr\nvalue = getattr(object(), 'a', None)",
    "import builtins\nsnapshot = dict(vars(builtins))\nsnapshot.update({'open': 1})",
    "values = {}\nvalues['open'] = 1\nvalues.update({'open': 2})",
    "builtins = {}\nbuiltins['open'] = None",
    "import operator\nvalues = {}\noperator.setitem(values, 'open', 1)",
    "import builtins\nname = getattr(builtins, '__name__')",
    "def audit(source):\n    return sorted(source)",
])
def test_allows_read_only_builtin_access_and_local_state(code: str) -> None:
    assert scan_source(code) == []
