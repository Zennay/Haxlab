from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "decimal.setcontext",
    "numpy.setbufsize",
    "numpy.seterr",
    "numpy.seterrcall",
    "numpy.set_printoptions",
    "torch.set_default_device",
    "torch.set_default_dtype",
    "torch.set_grad_enabled",
    "torch.set_num_interop_threads",
    "torch.set_num_threads",
    "torch.set_deterministic_debug_mode",
    "torch.use_deterministic_algorithms",
}

DECIMAL_CONTEXT_MUTATORS = {
    "clear_flags",
    "clear_traps",
    "__setattr__",
}

CONTAINER_MUTATORS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "__delitem__",
    "__ior__",
    "__setitem__",
}

UNBOUND_CONTEXT_CONTAINER_MUTATORS = {
    "dict.__delitem__",
    "dict.__ior__",
    "dict.__setitem__",
    "dict.clear",
    "dict.pop",
    "dict.popitem",
    "dict.setdefault",
    "dict.update",
    "operator.delitem",
    "operator.setitem",
}

TORCH_BACKEND_MUTABLE_ATTRIBUTES = {
    "torch.backends.cudnn.allow_tf32",
    "torch.backends.cudnn.benchmark",
    "torch.backends.cudnn.deterministic",
    "torch.backends.cuda.matmul.allow_fp16_reduced_precision_reduction",
    "torch.backends.cuda.matmul.allow_bf16_reduced_precision_reduction",
    "torch.backends.cuda.matmul.allow_tf32",
}


def _assignment_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_assignment_names(element))
        return names
    return []


def _imports(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {
        "getattr": "builtins.getattr",
        "setattr": "builtins.setattr",
        "delattr": "builtins.delattr",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _canonical(
    node: ast.AST | None,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return value_aliases.get(node.id, aliases.get(node.id, node.id))
    if isinstance(node, ast.Attribute):
        parent = _canonical(node.value, aliases, value_aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        accessor = _canonical(node.func, aliases, value_aliases)
        if (
            accessor == "builtins.getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _canonical(node.args[0], aliases, value_aliases)
            if owner:
                return f"{owner}.{node.args[1].value}"
    return None


def _resolve_aliases(
    tree: ast.Module,
    aliases: dict[str, str],
) -> dict[str, str]:
    values: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]
            if value is None:
                continue

            resolved = _canonical(value, aliases, values)
            if resolved is None:
                continue
            for target in targets:
                for name in _assignment_names(target):
                    if values.get(name) != resolved:
                        values[name] = resolved
                        changed = True
    return values


def _decimal_context_root(
    node: ast.AST | None,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None

    canonical = _canonical(node, aliases, value_aliases)
    if canonical and (
        canonical == "decimal.getcontext()"
        or canonical.startswith("decimal.getcontext().")
    ):
        return canonical

    if isinstance(node, ast.Call):
        target = _canonical(node.func, aliases, value_aliases)
        if target == "decimal.getcontext":
            return "decimal.getcontext()"

    if isinstance(node, ast.Attribute):
        parent = _decimal_context_root(node.value, aliases, value_aliases)
        if parent:
            return f"{parent}.{node.attr}"

    if isinstance(node, ast.Subscript):
        return _decimal_context_root(node.value, aliases, value_aliases)

    return None


def _decimal_aliases(
    tree: ast.Module,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
) -> dict[str, str]:
    roots: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]
            if value is None:
                continue

            root: str | None = None
            if isinstance(value, ast.Name) and value.id in roots:
                root = roots[value.id]
            else:
                root = _decimal_context_root(value, aliases, value_aliases)
            if root is None:
                continue

            for target in targets:
                for name in _assignment_names(target):
                    if roots.get(name) != root:
                        roots[name] = root
                        changed = True
    return roots


def _context_root(
    node: ast.AST | None,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
    decimal_aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name) and node.id in decimal_aliases:
        return decimal_aliases[node.id]
    if isinstance(node, ast.Attribute):
        parent = _context_root(
            node.value, aliases, value_aliases, decimal_aliases
        )
        if parent:
            return f"{parent}.{node.attr}"
    if isinstance(node, ast.Subscript):
        return _context_root(
            node.value, aliases, value_aliases, decimal_aliases
        )
    return _decimal_context_root(node, aliases, value_aliases)


def _mutation_context_target(
    node: ast.AST,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
    decimal_aliases: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Attribute):
        parent = _context_root(
            node.value, aliases, value_aliases, decimal_aliases
        )
        return f"{parent}.{node.attr}" if parent else None
    if isinstance(node, ast.Subscript):
        return _context_root(
            node.value, aliases, value_aliases, decimal_aliases
        )
    return None


def _context_mutator_name(
    node: ast.AST,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
    decimal_aliases: dict[str, str],
) -> str | None:
    owner: str | None = None
    method: str | None = None

    if isinstance(node, ast.Attribute):
        owner = _context_root(
            node.value, aliases, value_aliases, decimal_aliases
        )
        method = node.attr
    elif isinstance(node, ast.Call):
        accessor = _canonical(node.func, aliases, value_aliases)
        if (
            accessor == "builtins.getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _context_root(
                node.args[0], aliases, value_aliases, decimal_aliases
            )
            method = node.args[1].value

    if not owner or not method:
        return None
    if method in DECIMAL_CONTEXT_MUTATORS:
        return f"{owner}.{method}"
    if (
        (owner.endswith(".flags") or owner.endswith(".traps"))
        and method in CONTAINER_MUTATORS
    ):
        return f"{owner}.{method}"
    return None


def _context_mutator_aliases(
    tree: ast.Module,
    aliases: dict[str, str],
    value_aliases: dict[str, str],
    decimal_aliases: dict[str, str],
) -> dict[str, str]:
    mutators: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            value: ast.AST | None = None
            targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                value = node.value
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                value = node.value
                targets = [node.target]
            elif isinstance(node, ast.NamedExpr):
                value = node.value
                targets = [node.target]
            if value is None:
                continue

            resolved = (
                mutators.get(value.id)
                if isinstance(value, ast.Name)
                else _context_mutator_name(
                    value, aliases, value_aliases, decimal_aliases
                )
            )
            if not resolved:
                continue

            for target in targets:
                for name in _assignment_names(target):
                    if mutators.get(name) != resolved:
                        mutators[name] = resolved
                        changed = True
    return mutators


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _imports(tree)
    value_aliases = _resolve_aliases(tree, aliases)
    context_aliases = _decimal_aliases(tree, aliases, value_aliases)
    context_mutator_aliases = _context_mutator_aliases(
        tree, aliases, value_aliases, context_aliases
    )
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical(node.func, aliases, value_aliases)
            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: numeric runtime state mutation call: {target}"
                )
                continue

            if (
                isinstance(node.func, ast.Name)
                and node.func.id in context_mutator_aliases
            ):
                findings.append(
                    f"line {node.lineno}: decimal context mutation alias: "
                    f"{context_mutator_aliases[node.func.id]}"
                )
                continue

            reflected_context_mutator = _context_mutator_name(
                node.func, aliases, value_aliases, context_aliases
            )
            if reflected_context_mutator:
                findings.append(
                    f"line {node.lineno}: decimal context mutation call: "
                    f"{reflected_context_mutator}"
                )
                continue

            if (
                target in UNBOUND_CONTEXT_CONTAINER_MUTATORS
                and node.args
            ):
                root = _context_root(
                    node.args[0], aliases, value_aliases, context_aliases
                )
                if root and (
                    root.endswith(".flags") or root.endswith(".traps")
                ):
                    findings.append(
                        f"line {node.lineno}: decimal context container mutation: "
                        f"{target}({root}, ...)"
                    )
                    continue

            owner = None
            method = None
            if isinstance(node.func, ast.Attribute):
                method = node.func.attr
                owner = _context_root(
                    node.func.value, aliases, value_aliases, context_aliases
                )
            elif target and "." in target:
                method = target.rsplit(".", 1)[-1]

            if owner and method in DECIMAL_CONTEXT_MUTATORS:
                findings.append(
                    f"line {node.lineno}: decimal context mutation call: {owner}.{method}"
                )
                continue

            if owner and (
                owner.endswith(".flags") or owner.endswith(".traps")
            ) and method in CONTAINER_MUTATORS:
                findings.append(
                    f"line {node.lineno}: decimal context container mutation: "
                    f"{owner}.{method}"
                )

            if (
                target in {"builtins.setattr", "builtins.delattr"}
                and len(node.args) >= 2
            ):
                attr = node.args[1]
                if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                    guarded = _canonical(node.args[0], aliases, value_aliases)
                    if (
                        guarded
                        and f"{guarded}.{attr.value}"
                        in TORCH_BACKEND_MUTABLE_ATTRIBUTES
                    ):
                        findings.append(
                            f"line {node.lineno}: torch backend state mutation: "
                            f"{guarded}.{attr.value}"
                        )

                    context = _context_root(
                        node.args[0], aliases, value_aliases, context_aliases
                    )
                    if context:
                        findings.append(
                            f"line {node.lineno}: decimal context reflective mutation: "
                            f"{context}.{attr.value}"
                        )

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target_node in targets:
                canonical = _canonical(target_node, aliases, value_aliases)
                if canonical in TORCH_BACKEND_MUTABLE_ATTRIBUTES:
                    findings.append(
                        f"line {node.lineno}: torch backend state assignment: {canonical}"
                    )

                context = _mutation_context_target(
                    target_node, aliases, value_aliases, context_aliases
                )
                if context:
                    findings.append(
                        f"line {node.lineno}: decimal context state assignment: {context}"
                    )

        if isinstance(node, ast.Delete):
            for target_node in node.targets:
                canonical = _canonical(target_node, aliases, value_aliases)
                if canonical in TORCH_BACKEND_MUTABLE_ATTRIBUTES:
                    findings.append(
                        f"line {node.lineno}: torch backend state deletion: {canonical}"
                    )

                context = _mutation_context_target(
                    target_node, aliases, value_aliases, context_aliases
                )
                if context:
                    findings.append(
                        f"line {node.lineno}: decimal context state deletion: {context}"
                    )

    return sorted(set(findings))


def test_evaluation_package_has_no_numeric_runtime_state_mutation() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        )
        violations.extend(f"{relative}: {finding}" for finding in findings)

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import decimal\ndecimal.setcontext(decimal.Context())\n", "decimal.setcontext"),
        ("from decimal import setcontext as sc, Context\nsc(Context())\n", "decimal.setcontext"),
        ("import numpy as np\nnp.seterr(all='raise')\n", "numpy.seterr"),
        ("from numpy import set_printoptions as tune\ntune(precision=3)\n", "numpy.set_printoptions"),
        ("import torch as t\nt.set_default_dtype(t.float64)\n", "torch.set_default_dtype"),
        ("import torch\ntorch.set_num_threads(1)\n", "torch.set_num_threads"),
        ("import torch\ntorch.backends.cudnn.benchmark = True\n", "torch.backends.cudnn.benchmark"),
        ("import torch\nsetattr(torch.backends.cudnn, 'deterministic', True)\n", "torch.backends.cudnn.deterministic"),
        ("import decimal\nctx = decimal.getcontext()\nctx.prec = 50\n", "decimal.getcontext().prec"),
        ("from decimal import getcontext as gc\nctx = gc()\nctx.traps[ValueError] = True\n", "decimal.getcontext().traps"),
        ("import decimal\nctx = decimal.getcontext()\nctx.flags.clear()\n", "decimal.getcontext().flags.clear"),
        ("import decimal\nctx = decimal.getcontext()\nmutate = ctx.flags.clear\nmutate()\n", "decimal.getcontext().flags.clear"),
        ("import decimal\nctx = decimal.getcontext()\nmutate = getattr(ctx.traps, 'update')\nmutate({ValueError: True})\n", "decimal.getcontext().traps.update"),
        ("import decimal\nctx = decimal.getcontext()\ndict.update(ctx.flags, {ValueError: True})\n", "dict.update(decimal.getcontext().flags"),
        ("import decimal\nimport operator\nctx = decimal.getcontext()\noperator.setitem(ctx.traps, ValueError, True)\n", "operator.setitem(decimal.getcontext().traps"),
        ("import decimal\nctx = decimal.getcontext()\nsetattr(ctx, 'prec', 64)\n", "decimal.getcontext().prec"),
        ("import decimal\ngetattr(decimal, 'setcontext')(decimal.Context())\n", "decimal.setcontext"),
        ("import numpy as np\nmutate = np.seterr\nmutate(over='raise')\n", "numpy.seterr"),
    ],
)
def test_detector_rejects_numeric_runtime_state_mutation(
    source: str,
    expected: str,
) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import decimal\nwith decimal.localcontext() as ctx:\n    ctx.prec = 50\n",
        "import decimal\nctx = decimal.Context(prec=50)\nvalue = ctx.create_decimal('1.2')\n",
        "import numpy as np\nvalue = np.asarray([1.0, 2.0])\n",
        "import torch\nvalue = torch.tensor([1.0])\n",
        "import torch\ncurrent = torch.get_default_dtype()\n",
        "import decimal\ncurrent = decimal.getcontext().prec\n",
    ],
)
def test_detector_allows_local_or_read_only_numeric_configuration(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
