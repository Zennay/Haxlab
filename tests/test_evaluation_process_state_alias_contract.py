from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

TRACKED_MODULES = {"builtins", "locale", "operator", "os", "signal", "sys"}

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

GUARDED_ROOTS = {"os.environ", "sys.modules", "sys.path"}

ROOT_MUTATORS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "append",
    "extend",
    "insert",
    "remove",
    "reverse",
    "sort",
    "__setitem__",
    "__delitem__",
}

UNBOUND_MUTATORS = {
    "dict.clear",
    "dict.pop",
    "dict.popitem",
    "dict.setdefault",
    "dict.update",
    "dict.__setitem__",
    "dict.__delitem__",
    "list.append",
    "list.clear",
    "list.extend",
    "list.insert",
    "list.pop",
    "list.remove",
    "list.reverse",
    "list.sort",
    "list.__setitem__",
    "list.__delitem__",
    "operator.setitem",
    "operator.delitem",
}


def _evaluation_modules() -> list[Path]:
    return sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class AliasState:
    def __init__(self) -> None:
        self.scopes: list[dict[str, str | None]] = [{}]

    def push(self, blocked: set[str] | None = None) -> None:
        scope: dict[str, str | None] = {}
        for name in blocked or set():
            scope[name] = None
        self.scopes.append(scope)

    def pop(self) -> None:
        self.scopes.pop()

    def bind(self, name: str, target: str | None) -> None:
        self.scopes[-1][name] = target

    def resolve(self, name: str) -> str:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name] or name
        return name


class _FunctionLocalCollector(ast.NodeVisitor):
    """Collect bindings owned by one function scope without entering nested scopes."""

    def __init__(self) -> None:
        self.bound: set[str] = set()
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.bound.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.bound.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                self.bound.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.bound.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.bound.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bound.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bound.add(node.name)
        for statement in node.body:
            self.visit(statement)

    def local_names(self) -> set[str]:
        return self.bound - self.global_names - self.nonlocal_names


def _function_local_names(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> set[str]:
    collector = _FunctionLocalCollector()
    for statement in node.body:
        collector.visit(statement)
    return collector.local_names()


class ProcessStateAliasVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases = AliasState()
        self.findings: list[str] = []

    def canonical(self, node: ast.AST | None) -> str | None:
        if node is None:
            return None
        if isinstance(node, ast.Name):
            return self.aliases.resolve(node.id)
        if isinstance(node, ast.Attribute):
            parent = self.canonical(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        if isinstance(node, ast.Call):
            accessor = self.canonical(node.func)
            if (
                accessor in {"getattr", "builtins.getattr"}
                and len(node.args) >= 2
            ):
                attribute = _constant_string(node.args[1])
                owner = self.canonical(node.args[0])
                if owner and attribute:
                    return f"{owner}.{attribute}"
        if isinstance(node, ast.Subscript):
            return self.canonical(node.value)
        if isinstance(node, ast.NamedExpr):
            return self.canonical(node.value)
        return None

    @staticmethod
    def _simple_targets(node: ast.AST) -> list[str]:
        if isinstance(node, ast.Name):
            return [node.id]
        if isinstance(node, (ast.Tuple, ast.List)):
            result: list[str] = []
            for element in node.elts:
                result.extend(ProcessStateAliasVisitor._simple_targets(element))
            return result
        return []

    @staticmethod
    def _is_trackable(target: str | None) -> bool:
        if not target:
            return False
        if target in FORBIDDEN_CALLS or target in GUARDED_ROOTS:
            return True

        normalized = target[len("builtins.") :] if target.startswith("builtins.") else target
        if normalized in {
            "delattr",
            "dict",
            "getattr",
            "list",
            "setattr",
        } or target in {
            "builtins.getattr",
            "builtins",
            "locale",
            "operator",
            "os",
            "signal",
            "sys",
        }:
            return True
        if normalized in UNBOUND_MUTATORS:
            return True

        return any(
            target == f"{root}.{method}"
            for root in GUARDED_ROOTS
            for method in ROOT_MUTATORS
        )

    @staticmethod
    def _normalize_builtin(target: str | None) -> str | None:
        if target and target.startswith("builtins."):
            return target[len("builtins.") :]
        return target

    def _record_target_mutation(
        self,
        node: ast.AST,
        target: ast.AST,
        *,
        allow_name: bool = False,
    ) -> None:
        # Plain-name assignment/deletion only rebinds or removes the alias itself.
        # Augmented assignment is different: e.g. os.environ aliases can mutate
        # their underlying process-global object through in-place operators.
        if isinstance(target, ast.Name) and not allow_name:
            return
        root = self.canonical(target)
        if root in GUARDED_ROOTS:
            self.findings.append(
                f"line {getattr(node, 'lineno', 0)}: aliased process-state target mutation: {root}"
            )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in TRACKED_MODULES:
                self.aliases.bind(alias.asname or root, alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    self.aliases.bind(
                        alias.asname or alias.name,
                        f"{node.module}.{alias.name}",
                    )
        self.generic_visit(node)

    def _bind_assignment(self, targets: list[ast.AST], value: ast.AST) -> None:
        resolved = self.canonical(value)
        for target in targets:
            for name in self._simple_targets(target):
                self.aliases.bind(name, resolved if self._is_trackable(resolved) else None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._record_target_mutation(node, target)
        self._bind_assignment(list(node.targets), node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._record_target_mutation(node, node.target)
        if node.value is not None:
            self._bind_assignment([node.target], node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_assignment([node.target], node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        self._record_target_mutation(node, node.target, allow_name=True)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._record_target_mutation(node, target)

    def visit_Call(self, node: ast.Call) -> None:
        target = self.canonical(node.func)
        normalized = self._normalize_builtin(target)

        if normalized in {"setattr", "delattr"} and len(node.args) >= 2:
            owner = self.canonical(node.args[0])
            attribute = _constant_string(node.args[1])
            guarded_attribute = (
                (owner == "os" and attribute == "environ")
                or (owner == "sys" and attribute in {"modules", "path"})
            )
            if guarded_attribute:
                self.findings.append(
                    f"line {node.lineno}: reflective process-state mutation: "
                    f"{normalized}({owner}.{attribute})"
                )

        if target in FORBIDDEN_CALLS:
            self.findings.append(
                f"line {node.lineno}: aliased process-state mutation call: {target}"
            )
        elif target:
            for root in GUARDED_ROOTS:
                prefix = f"{root}."
                if target.startswith(prefix) and target[len(prefix) :] in ROOT_MUTATORS:
                    self.findings.append(
                        f"line {node.lineno}: aliased process-state mutation method: {target}"
                    )
                    break

        if normalized in UNBOUND_MUTATORS and node.args:
            owner = self.canonical(node.args[0])
            if owner in GUARDED_ROOTS:
                self.findings.append(
                    f"line {node.lineno}: unbound process-state mutation: {normalized}({owner}, ...)"
                )

        self.generic_visit(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        blocked = _function_local_names(node)
        blocked.update(
            argument.arg
            for argument in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
        )
        if node.args.vararg:
            blocked.add(node.args.vararg.arg)
        if node.args.kwarg:
            blocked.add(node.args.kwarg.arg)

        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in list(node.args.defaults) + [
            value for value in node.args.kw_defaults if value is not None
        ]:
            self.visit(default)

        self.aliases.push(blocked)
        for statement in node.body:
            self.visit(statement)
        self.aliases.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in list(node.args.defaults) + [
            value for value in node.args.kw_defaults if value is not None
        ]:
            self.visit(default)

        blocked = {
            argument.arg
            for argument in (
                list(node.args.posonlyargs)
                + list(node.args.args)
                + list(node.args.kwonlyargs)
            )
        }
        if node.args.vararg:
            blocked.add(node.args.vararg.arg)
        if node.args.kwarg:
            blocked.add(node.args.kwarg.arg)

        self.aliases.push(blocked)
        self.visit(node.body)
        self.aliases.pop()

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        values: list[ast.AST],
    ) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return

        # Python evaluates the first iterable in the enclosing scope. Generator
        # targets themselves live in the comprehension scope.
        self.visit(generators[0].iter)
        blocked: set[str] = set()
        for generator in generators:
            blocked.update(self._simple_targets(generator.target))

        self.aliases.push(blocked)
        for condition in generators[0].ifs:
            self.visit(condition)
        for generator in generators[1:]:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.aliases.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        self.aliases.push()
        for statement in node.body:
            self.visit(statement)
        self.aliases.pop()


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = ProcessStateAliasVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_package_has_no_process_state_alias_mutation() -> None:
    failures: list[str] = []
    modules = _evaluation_modules()
    assert modules, "expected evaluation Python modules"

    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        failures.extend(f"{relative}: {finding}" for finding in findings)

    assert failures == [], "\n".join(failures)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import os\ncd = os.chdir\ncd('/tmp')\n", "os.chdir"),
        (
            "import os\nenv = os.environ\nmutate = env.update\nmutate({'MODE': 'unsafe'})\n",
            "os.environ.update",
        ),
        (
            "import sys\npaths = sys.path\npaths.append('/tmp/plugin')\n",
            "sys.path.append",
        ),
        (
            "import sys\nloaded = sys.modules\ndict.pop(loaded, 'haxlab.plugin', None)\n",
            "dict.pop(sys.modules",
        ),
        (
            "import os\nfrom builtins import dict as Mapping\nenv = getattr(os, 'environ')\nMapping.__setitem__(env, 'MODE', 'unsafe')\n",
            "dict.__setitem__(os.environ",
        ),
        (
            "import operator\nimport sys\npaths = sys.path\nwrite = operator.setitem\nwrite(paths, 0, '/tmp')\n",
            "operator.setitem(sys.path",
        ),
        (
            "import os\nstate = os\nenv = state.environ\npop = dict.pop\npop(env, 'MODE', None)\n",
            "dict.pop(os.environ",
        ),
        (
            "import sys\nsystem = sys\npaths = system.path\nmutate = list.append\nmutate(paths, '/tmp')\n",
            "list.append(sys.path",
        ),
        (
            "import sys\nsystem = sys\nmutate = setattr\nmutate(system, 'path', [])\n",
            "setattr(sys.path",
        ),
        (
            "import os\nstate = os\nremove = delattr\nremove(state, 'environ')\n",
            "delattr(os.environ",
        ),
        (
            "import os\nenv = os.environ\nenv |= {'MODE': 'unsafe'}\n",
            "os.environ",
        ),
        (
            "import os\n(mut := os.chdir)('/tmp')\n",
            "os.chdir",
        ),
        (
            "import os\n(env := os.environ).update({'MODE': 'unsafe'})\n",
            "os.environ.update",
        ),
        (
            "from sys import settrace as trace\nalias = trace\nalias(lambda *args: None)\n",
            "sys.settrace",
        ),
    ],
)
def test_contract_rejects_assignment_and_bound_alias_bypasses(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import os\nenv = os.environ\nvalue = env.get('MODE')\n",
        "import sys\npaths = sys.path\nvalue = tuple(paths)\n",
        "import sys\nloaded = sys.modules\nvalue = loaded.get('haxlab')\n",
        (
            "import os\n"
            "env = os.environ\n"
            "def probe(env):\n"
            "    env.update({'local': True})\n"
        ),
        (
            "import os\n"
            "def first():\n"
            "    env = os.environ\n"
            "    return env.get('MODE')\n"
            "def second():\n"
            "    env = {}\n"
            "    env.update({'local': True})\n"
        ),
        (
            "mutate = setattr\n"
            "class Local:\n"
            "    pass\n"
            "obj = Local()\n"
            "mutate(obj, 'value', 1)\n"
        ),
        (
            "import os\n"
            "env = os.environ\n"
            "def probe():\n"
            "    value = env.get('MODE')\n"
            "    env = {}\n"
            "    env.update({'local': True})\n"
            "    return value\n"
        ),
        (
            "import sys\n"
            "paths = sys.path\n"
            "paths = []\n"
            "paths.append('/local-only')\n"
        ),
        (
            "import os\n"
            "env = os.environ\n"
            "del env\n"
        ),
        (
            "import os\n"
            "env = os.environ\n"
            "probe = lambda env: env.update({'local': True})\n"
        ),
        (
            "import os\n"
            "env = os.environ\n"
            "values = [env.update({'local': True}) for env in ({},)]\n"
        ),
    ],
)
def test_contract_preserves_read_only_or_shadowed_local_aliases(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
