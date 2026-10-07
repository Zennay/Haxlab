from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"
PACKAGE = "haxlab.evaluation"


def _module_name(path: Path) -> str:
    relative = path.relative_to(EVALUATION_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join([PACKAGE, *parts]) if parts else PACKAGE


def _module_package(module_name: str, path: Path) -> str:
    return module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]


class RuntimeImportVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        module_name: str,
        package_name: str,
        known_modules: set[str],
    ) -> None:
        self.module_name = module_name
        self.package_name = package_name
        self.known_modules = known_modules
        self.dependencies: set[str] = set()
        self.module_aliases: dict[str, str] = {}
        self.type_checking_names: set[str] = {"TYPE_CHECKING", "typing.TYPE_CHECKING"}

    def _record_target(self, target: str, imported_names: list[str] | None = None) -> None:
        if target in self.known_modules and target != self.module_name:
            self.dependencies.add(target)

        if imported_names:
            for name in imported_names:
                child = f"{target}.{name}"
                if child in self.known_modules and child != self.module_name:
                    self.dependencies.add(child)

    def _resolve_from(self, node: ast.ImportFrom) -> str | None:
        if node.level == 0:
            return node.module

        parts = self.package_name.split(".")
        up = node.level - 1
        if up >= len(parts):
            return None
        base = ".".join(parts[: len(parts) - up])
        return f"{base}.{node.module}" if node.module else base

    def _name(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Name):
            return self.module_aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            parent = self._name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return None

    def _type_checking_polarity(self, node: ast.AST) -> bool | None:
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            inner = self._type_checking_polarity(node.operand)
            return None if inner is None else not inner
        name = self._name(node)
        if name in self.type_checking_names:
            return True
        return None

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            self.module_aliases[alias.asname or root] = alias.name
            self._record_target(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        target = self._resolve_from(node)
        if target is None:
            return

        imported_names = [alias.name for alias in node.names if alias.name != "*"]
        self._record_target(target, imported_names)

        if target == "typing":
            for alias in node.names:
                if alias.name == "TYPE_CHECKING":
                    local = alias.asname or alias.name
                    self.type_checking_names.add(local)

    def visit_If(self, node: ast.If) -> None:
        polarity = self._type_checking_polarity(node.test)
        if polarity is True:
            for child in node.orelse:
                self.visit(child)
            return
        if polarity is False:
            for child in node.body:
                self.visit(child)
            return
        self.generic_visit(node)


def _runtime_dependencies(
    source: str,
    *,
    module_name: str,
    package_name: str,
    known_modules: set[str],
) -> set[str]:
    tree = ast.parse(source)
    visitor = RuntimeImportVisitor(
        module_name=module_name,
        package_name=package_name,
        known_modules=known_modules,
    )
    visitor.visit(tree)
    return visitor.dependencies


def _runtime_graph() -> dict[str, set[str]]:
    paths = sorted(EVALUATION_ROOT.rglob("*.py"))
    modules = {_module_name(path): path for path in paths}
    known_modules = set(modules)

    graph: dict[str, set[str]] = {}
    for module_name, path in sorted(modules.items()):
        graph[module_name] = _runtime_dependencies(
            path.read_text(encoding="utf-8"),
            module_name=module_name,
            package_name=_module_package(module_name, path),
            known_modules=known_modules,
        )
    return graph


def _first_cycle(graph: dict[str, set[str]]) -> tuple[str, ...] | None:
    state: dict[str, int] = {}
    stack: list[str] = []
    positions: dict[str, int] = {}

    def visit(node: str) -> tuple[str, ...] | None:
        state[node] = 1
        positions[node] = len(stack)
        stack.append(node)

        for dependency in sorted(graph.get(node, ())):
            if dependency not in graph:
                continue
            if state.get(dependency, 0) == 0:
                cycle = visit(dependency)
                if cycle is not None:
                    return cycle
            elif state.get(dependency) == 1:
                start = positions[dependency]
                return tuple(stack[start:] + [dependency])

        stack.pop()
        positions.pop(node, None)
        state[node] = 2
        return None

    for node in sorted(graph):
        if state.get(node, 0) == 0:
            cycle = visit(node)
            if cycle is not None:
                return cycle
    return None


def test_evaluation_runtime_import_graph_is_acyclic() -> None:
    graph = _runtime_graph()
    assert graph, "expected evaluation Python modules"

    cycle = _first_cycle(graph)
    assert cycle is None, "evaluation runtime import cycle: " + " -> ".join(cycle or ())


def test_cycle_detector_reports_direct_and_transitive_cycles() -> None:
    assert _first_cycle({"a": {"a"}}) == ("a", "a")
    assert _first_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}}) == (
        "a",
        "b",
        "c",
        "a",
    )


def test_runtime_import_extraction_handles_absolute_and_relative_modules() -> None:
    known = {
        "haxlab.evaluation.probe",
        "haxlab.evaluation.models",
        "haxlab.evaluation.duel_gate",
    }
    source = """
from . import models
from .duel_gate import decide_duel_gate
"""
    assert _runtime_dependencies(
        source,
        module_name="haxlab.evaluation.probe",
        package_name="haxlab.evaluation",
        known_modules=known,
    ) == {
        "haxlab.evaluation.models",
        "haxlab.evaluation.duel_gate",
    }


def test_type_checking_only_imports_do_not_create_runtime_edges() -> None:
    known = {
        "haxlab.evaluation.probe",
        "haxlab.evaluation.models",
        "haxlab.evaluation.duel_gate",
    }
    source = """
from typing import TYPE_CHECKING as TC

if TC:
    from .models import PromotionDecision

if not TC:
    from .duel_gate import decide_duel_gate
"""
    assert _runtime_dependencies(
        source,
        module_name="haxlab.evaluation.probe",
        package_name="haxlab.evaluation",
        known_modules=known,
    ) == {"haxlab.evaluation.duel_gate"}
