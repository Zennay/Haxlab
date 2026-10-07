from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

TRACKED_ROOTS = {"builtins", "numpy", "random", "torch"}

FORBIDDEN_CALLS = {
    "random.seed",
    "random.setstate",
    "numpy.random.seed",
    "numpy.random.set_state",
    "torch.manual_seed",
    "torch.seed",
    "torch.set_rng_state",
    "torch.random.manual_seed",
    "torch.random.seed",
    "torch.random.set_rng_state",
    "torch.cuda.manual_seed",
    "torch.cuda.manual_seed_all",
    "torch.cuda.set_rng_state",
    "torch.cuda.set_rng_state_all",
}

TRACKABLE_OBJECTS = {
    "builtins",
    "builtins.getattr",
    "getattr",
    "numpy",
    "numpy.random",
    "random",
    "torch",
    "torch.random",
    "torch.cuda",
} | FORBIDDEN_CALLS


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

    def resolve(self, name: str) -> str | None:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
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

    def _visit_comprehension(
        self,
        generators: list[ast.comprehension],
        values: list[ast.AST],
    ) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return

        # The first iterable is evaluated in the enclosing scope. Each target
        # shadows outer names only after its own iterable has been evaluated.
        self.visit(generators[0].iter)
        self.aliases.push()
        for name in self._simple_targets(generators[0].target):
            self.aliases.bind(name, None)
        for condition in generators[0].ifs:
            self.visit(condition)

        for generator in generators[1:]:
            self.visit(generator.iter)
            for name in self._simple_targets(generator.target):
                self.aliases.bind(name, None)
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


class RngStateVisitor(ast.NodeVisitor):
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
            return f"{parent}.{node.attr}" if parent else None
        if isinstance(node, ast.Call):
            accessor = self.canonical(node.func)
            if accessor in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                owner = self.canonical(node.args[0])
                attribute = _constant_string(node.args[1])
                if owner and attribute:
                    return f"{owner}.{attribute}"
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
                result.extend(RngStateVisitor._simple_targets(element))
            return result
        return []

    @staticmethod
    def _is_trackable(target: str | None) -> bool:
        if not target:
            return False
        if target in TRACKABLE_OBJECTS:
            return True
        return any(
            target.startswith(prefix)
            for prefix in ("numpy.random.", "random.", "torch.random.", "torch.cuda.")
        )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root not in TRACKED_ROOTS:
                continue
            bound_name = alias.asname or root
            canonical = alias.name if alias.asname else root
            self.aliases.bind(bound_name, canonical)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".", 1)[0]
        if root in TRACKED_ROOTS:
            for alias in node.names:
                if alias.name == "*":
                    self.findings.append(
                        f"line {node.lineno}: wildcard RNG-state import: {module}.*"
                    )
                    continue
                self.aliases.bind(
                    alias.asname or alias.name,
                    f"{module}.{alias.name}" if module else alias.name,
                )
        self.generic_visit(node)

    def _bind_assignment(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, ast.Name):
            resolved = self.canonical(value)
            self.aliases.bind(
                target.id,
                resolved if self._is_trackable(resolved) else None,
            )
            return

        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for child_target, child_value in zip(target.elts, value.elts):
                self._bind_assignment(child_target, child_value)
            return

        for name in self._simple_targets(target):
            self.aliases.bind(name, None)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self._bind_assignment(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
            self._bind_assignment(node.target, node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._bind_assignment(node.target, node.value)

    def visit_Call(self, node: ast.Call) -> None:
        target = self.canonical(node.func)
        if target in FORBIDDEN_CALLS:
            self.findings.append(
                f"line {node.lineno}: process-global RNG state mutation: {target}"
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
    visitor = RngStateVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.findings))


def test_evaluation_package_has_no_process_global_rng_state_mutation() -> None:
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
        ("import random\nrandom.seed(7)\n", "random.seed"),
        ("from random import setstate as restore\nrestore(state)\n", "random.setstate"),
        ("import random as rng\nseed = rng.seed\nseed(7)\n", "random.seed"),
        ("import random\ngetattr(random, 'seed')(7)\n", "random.seed"),
        ("from builtins import getattr as attr\nimport random\nattr(random, 'seed')(7)\n", "random.seed"),
        ("import numpy as np\nnp.random.seed(7)\n", "numpy.random.seed"),
        ("from numpy import random as nr\nreseed = nr.seed\nreseed(7)\n", "numpy.random.seed"),
        ("from numpy.random import set_state as restore\nrestore(state)\n", "numpy.random.set_state"),
        ("import torch\ntorch.manual_seed(7)\n", "torch.manual_seed"),
        ("from torch import seed as reseed\nreseed()\n", "torch.seed"),
        ("import torch\nrandom_api = torch.random\nrandom_api.set_rng_state(state)\n", "torch.random.set_rng_state"),
        ("import torch\ncuda = torch.cuda\ncuda.manual_seed_all(7)\n", "torch.cuda.manual_seed_all"),
        ("from torch.cuda import set_rng_state as restore\nrestore(state)\n", "torch.cuda.set_rng_state"),
        ("import torch\n(getattr(torch.cuda, 'set_rng_state_all'))(states)\n", "torch.cuda.set_rng_state_all"),
        ("import torch\n(mut := torch.manual_seed)(7)\n", "torch.manual_seed"),
        (
            "import random\n"
            "(seed, restore) = (random.seed, random.setstate)\n"
            "seed(7)\n",
            "random.seed",
        ),
        (
            "import numpy as np\n"
            "[reseed, restore] = [np.random.seed, np.random.set_state]\n"
            "restore(state)\n",
            "numpy.random.set_state",
        ),
    ],
)
def test_contract_rejects_global_rng_state_mutation(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import random\nrng = random.Random(7)\nvalue = rng.random()\n",
        "import random\nrng = random.Random(7)\nrng.seed(8)\n",
        "import random\nstate = random.getstate()\n",
        "import numpy as np\nrng = np.random.default_rng(7)\nvalue = rng.integers(10)\n",
        "import numpy as np\nstate = np.random.get_state()\n",
        "import torch\ngenerator = torch.Generator()\ngenerator.manual_seed(7)\n",
        "import torch\nstate = torch.get_rng_state()\n",
        "import torch\nstates = torch.cuda.get_rng_state_all()\n",
        (
            "import random\n"
            "def probe(random):\n"
            "    random.seed(7)\n"
        ),
        (
            "import random\n"
            "def probe():\n"
            "    random = LocalRng()\n"
            "    random.seed(7)\n"
        ),
        (
            "import random\n"
            "values = [random.seed(7) for random in (LocalRng(),)]\n"
        ),
        (
            "import torch\n"
            "values = {torch.manual_seed(7) for torch in (LocalGenerator(),)}\n"
        ),
    ],
)
def test_contract_preserves_local_rngs_and_read_only_state(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []


@pytest.mark.parametrize(
    "source",
    [
        "from random import *\n",
        "from numpy.random import *\n",
        "from torch.cuda import *\n",
    ],
)
def test_contract_rejects_ambiguous_rng_wildcard_imports(source: str) -> None:
    findings = "\n".join(scan_source(source))
    assert "wildcard RNG-state import" in findings
