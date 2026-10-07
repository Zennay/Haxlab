from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
AUDIT_ROOTS = (
    SRC_ROOT / "haxlab" / "ingestion",
    SRC_ROOT / "haxlab" / "learning",
    SRC_ROOT / "haxlab" / "runtime",
)


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(SRC_ROOT).with_suffix("").parts)


def _module_package(module_name: str, path: Path) -> str:
    return module_name if path.name == "__init__.py" else module_name.rpartition(".")[0]


class RuntimeAuditImportVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        package_name: str,
        known_modules: set[str],
    ) -> None:
        self.package_name = package_name
        self.known_modules = known_modules
        self.dependencies: set[str] = set()
        self.module_aliases: dict[str, str] = {}
        self.type_checking_names: set[str] = {"TYPE_CHECKING", "typing.TYPE_CHECKING"}

    def _record_target(
        self,
        target: str,
        imported_names: list[str] | None = None,
    ) -> None:
        child_modules = {
            f"{target}.{name}"
            for name in (imported_names or [])
            if f"{target}.{name}" in self.known_modules
        }
        if child_modules:
            self.dependencies.update(child_modules)
            return

        if target in self.known_modules:
            self.dependencies.add(target)

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
                    self.type_checking_names.add(alias.asname or alias.name)

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
    package_name: str,
    known_modules: set[str],
) -> set[str]:
    tree = ast.parse(source)
    visitor = RuntimeAuditImportVisitor(
        package_name=package_name,
        known_modules=known_modules,
    )
    visitor.visit(tree)
    return visitor.dependencies


def _runtime_graph() -> dict[str, set[str]]:
    paths = _audit_paths()
    modules = {_module_name(path): path for path in paths}
    known_modules = set(modules)

    graph: dict[str, set[str]] = {}
    for module_name, path in sorted(modules.items()):
        graph[module_name] = _runtime_dependencies(
            path.read_text(encoding="utf-8"),
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


def test_data_pipeline_auditor_runtime_import_graph_is_acyclic() -> None:
    graph = _runtime_graph()
    assert graph, "expected data-pipeline *_audit.py modules"

    cycle = _first_cycle(graph)
    assert cycle is None, "data-pipeline auditor runtime import cycle: " + " -> ".join(
        cycle or ()
    )


def test_cycle_detector_reports_direct_and_transitive_cycles() -> None:
    assert _first_cycle({"a": {"a"}}) == ("a", "a")
    assert _first_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}}) == (
        "a",
        "b",
        "c",
        "a",
    )


def test_runtime_import_extraction_preserves_self_import_edges() -> None:
    module = "haxlab.learning.probe_audit"
    assert _runtime_dependencies(
        "from haxlab.learning.probe_audit import helper\n",
        package_name="haxlab.learning",
        known_modules={module},
    ) == {module}


def test_runtime_import_extraction_handles_absolute_relative_and_child_modules() -> None:
    known = {
        "haxlab.learning.manifest_audit",
        "haxlab.learning.manifest_source_audit",
        "haxlab.learning.shard_audit",
        "haxlab.learning.shard_bundle_audit",
    }
    source = """
from haxlab.learning import manifest_audit
from .shard_audit import audit_shard
from . import shard_bundle_audit
"""
    assert _runtime_dependencies(
        source,
        package_name="haxlab.learning",
        known_modules=known,
    ) == {
        "haxlab.learning.manifest_audit",
        "haxlab.learning.shard_audit",
        "haxlab.learning.shard_bundle_audit",
    }


def test_type_checking_only_imports_do_not_create_runtime_edges() -> None:
    known = {
        "haxlab.learning.manifest_audit",
        "haxlab.learning.manifest_source_audit",
        "haxlab.learning.shard_audit",
    }
    source = """
import typing as t
from typing import TYPE_CHECKING as TC

if TC:
    from .manifest_audit import audit_manifest

if t.TYPE_CHECKING:
    from .manifest_source_audit import audit_sources

if not TC:
    from .shard_audit import audit_shard
"""
    assert _runtime_dependencies(
        source,
        package_name="haxlab.learning",
        known_modules=known,
    ) == {"haxlab.learning.shard_audit"}
