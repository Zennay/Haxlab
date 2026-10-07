from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BUILTIN_HELPERS = {"delattr", "getattr", "globals", "setattr", "vars"}
BUILTIN_TYPES = {"dict"}
IMPLICIT_ALIASES = {
    name: f"builtins.{name}"
    for name in BUILTIN_HELPERS | BUILTIN_TYPES
}
MUTATING_MAPPING_METHODS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "__setitem__",
    "__delitem__",
    "__ior__",
}
OPERATOR_MUTATORS = {
    "operator.setitem",
    "operator.delitem",
    "operator.ior",
}
SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
MODULE_GLOBALS = "<module-globals>"


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None

    if isinstance(node, ast.Name):
        if node.id == "__builtins__":
            return "builtins.__dict__"
        return aliases.get(node.id)

    if isinstance(node, ast.Attribute):
        owner = _canonical_name(node.value, aliases)
        return f"{owner}.{node.attr}" if owner else None

    if isinstance(node, ast.Subscript):
        owner = _canonical_name(node.value, aliases)
        key = _constant_string(node.slice)
        if owner == "sys.modules" and key == "builtins":
            return "builtins"
        if owner == MODULE_GLOBALS and key == "__builtins__":
            return "builtins.__dict__"
        return None

    if isinstance(node, ast.Call):
        target = _canonical_name(node.func, aliases)

        if (
            target == "builtins.getattr"
            and len(node.args) >= 2
            and (attribute := _constant_string(node.args[1])) is not None
        ):
            owner = _canonical_name(node.args[0], aliases)
            if owner:
                return f"{owner}.{attribute}"

        if target == "builtins.vars" and len(node.args) == 1:
            owner = _canonical_name(node.args[0], aliases)
            if owner == "builtins":
                return "builtins.__dict__"

        if target == "builtins.globals" and not node.args and not node.keywords:
            return MODULE_GLOBALS

        if (
            target in {"sys.modules.get", f"{MODULE_GLOBALS}.get"}
            and node.args
        ):
            key = _constant_string(node.args[0])
            if target == "sys.modules.get" and key == "builtins":
                return "builtins"
            if target == f"{MODULE_GLOBALS}.get" and key == "__builtins__":
                return "builtins.__dict__"

    return None


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_assignment_names(element))
        return names
    return []


def _scope_nodes(root: ast.AST) -> list[ast.AST]:
    nodes: list[ast.AST] = []

    def visit(node: ast.AST, *, scope_root: bool = False) -> None:
        nodes.append(node)
        if not scope_root and isinstance(node, SCOPE_NODES):
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(root, scope_root=True)
    return nodes


def _function_locals(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda,
) -> tuple[set[str], set[str]]:
    nodes = _scope_nodes(node)
    globals_: set[str] = set()
    for current in nodes:
        if isinstance(current, ast.Global):
            globals_.update(current.names)

    locals_: set[str] = set()
    args = node.args
    for argument in (
        list(args.posonlyargs)
        + list(args.args)
        + list(args.kwonlyargs)
    ):
        locals_.add(argument.arg)
    if args.vararg:
        locals_.add(args.vararg.arg)
    if args.kwarg:
        locals_.add(args.kwarg.arg)

    for current in nodes:
        if isinstance(current, ast.Name) and isinstance(current.ctx, ast.Store):
            if current.id not in globals_:
                locals_.add(current.id)
        elif (
            current is not node
            and isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and current.name not in globals_
        ):
            locals_.add(current.name)
        elif isinstance(current, ast.Import):
            for alias in current.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                if local not in globals_:
                    locals_.add(local)
        elif isinstance(current, ast.ImportFrom):
            for alias in current.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                if local not in globals_:
                    locals_.add(local)

    return locals_, globals_


def _collect_scope_aliases(
    nodes: list[ast.AST],
    *,
    inherited: dict[str, str],
) -> dict[str, str]:
    aliases = dict(inherited)

    for node in nodes:
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in {"builtins", "operator", "sys"}:
                    aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module in {
            "builtins",
            "operator",
            "sys",
        }:
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )

    # Resolve ordinary assignment aliases such as:
    #   namespace = builtins.__dict__
    #   put = namespace.__setitem__
    # A bounded fixed point is sufficient because aliases only move toward an
    # already canonical object and the production tree is finite.
    for _ in range(16):
        changed = False
        for node in nodes:
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

            resolved = _canonical_name(value, aliases)
            if resolved is None:
                continue

            for target in targets:
                for local in _assignment_names(target):
                    if aliases.get(local) != resolved:
                        aliases[local] = resolved
                        changed = True

        if not changed:
            break

    return aliases


def _target_root(node: ast.AST, aliases: dict[str, str]) -> str | None:
    # Assigning to a plain local name only rebinds the alias. Subscript and
    # attribute targets mutate the referenced object.
    if isinstance(node, ast.Name):
        return None
    if isinstance(node, ast.Attribute):
        return _canonical_name(node, aliases)
    if isinstance(node, ast.Subscript):
        return _canonical_name(node.value, aliases)
    return None


def _augmented_target_root(
    node: ast.AST,
    aliases: dict[str, str],
) -> str | None:
    # In-place operators on an alias can mutate the referenced dictionary, so
    # a Name target is significant for AugAssign even though it is not for
    # ordinary assignment.
    if isinstance(node, ast.Name):
        return _canonical_name(node, aliases)
    return _target_root(node, aliases)


def _is_builtin_namespace_mutation_root(name: str | None) -> bool:
    return bool(
        name
        and (
            name == "builtins"
            or name.startswith("builtins.")
        )
    )


def _scan_nodes(
    nodes: list[ast.AST],
    *,
    aliases: dict[str, str],
) -> list[str]:
    findings: list[str] = []

    for node in nodes:
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)

            if (
                target in {"builtins.setattr", "builtins.delattr"}
                and node.args
                and _canonical_name(node.args[0], aliases) == "builtins"
            ):
                findings.append(
                    f"line {node.lineno}: builtins namespace mutation: {target}"
                )
                continue

            if target:
                for method in MUTATING_MAPPING_METHODS:
                    if target == f"builtins.__dict__.{method}":
                        findings.append(
                            f"line {node.lineno}: builtins mapping mutation: {target}"
                        )
                        break

            if (
                target in OPERATOR_MUTATORS
                and node.args
                and _canonical_name(node.args[0], aliases)
                == "builtins.__dict__"
            ):
                findings.append(
                    f"line {node.lineno}: builtins mapping mutation: {target}"
                )

            if (
                target
                and target.startswith("builtins.dict.")
                and target.rsplit(".", 1)[-1] in MUTATING_MAPPING_METHODS
                and node.args
                and _canonical_name(node.args[0], aliases)
                == "builtins.__dict__"
            ):
                findings.append(
                    f"line {node.lineno}: builtins mapping mutation: {target}"
                )

        if isinstance(node, ast.Assign):
            for target_node in node.targets:
                root = _target_root(target_node, aliases)
                if _is_builtin_namespace_mutation_root(root):
                    findings.append(
                        f"line {node.lineno}: builtins namespace assignment: {root}"
                    )

        elif isinstance(node, ast.AnnAssign):
            root = _target_root(node.target, aliases)
            if _is_builtin_namespace_mutation_root(root):
                findings.append(
                    f"line {node.lineno}: builtins namespace assignment: {root}"
                )

        elif isinstance(node, ast.AugAssign):
            root = _augmented_target_root(node.target, aliases)
            if _is_builtin_namespace_mutation_root(root):
                findings.append(
                    f"line {node.lineno}: builtins namespace augmented assignment: {root}"
                )

        elif isinstance(node, ast.Delete):
            for target_node in node.targets:
                root = _target_root(target_node, aliases)
                if _is_builtin_namespace_mutation_root(root):
                    findings.append(
                        f"line {node.lineno}: builtins namespace deletion: {root}"
                    )

    return findings


def _scan_scope(
    root: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef,
    *,
    inherited_aliases: dict[str, str],
) -> list[str]:
    nodes = _scope_nodes(root)
    visible = dict(inherited_aliases)

    if isinstance(root, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        locals_, globals_ = _function_locals(root)
        visible = {
            name: canonical
            for name, canonical in visible.items()
            if name not in locals_ or name in globals_
        }

    aliases = _collect_scope_aliases(nodes, inherited=visible)
    findings = _scan_nodes(nodes, aliases=aliases)

    # Function/lambda bodies can close over aliases from an enclosing function.
    # Class-local names are different: methods do not resolve unqualified names
    # through the class namespace, so nested scopes below a class inherit the
    # lexical aliases that were visible before the class body instead.
    nested_inherited = (
        inherited_aliases if isinstance(root, ast.ClassDef) else aliases
    )
    for node in nodes:
        if node is root or not isinstance(node, SCOPE_NODES):
            continue
        findings.extend(
            _scan_scope(
                node,
                inherited_aliases=nested_inherited,
            )
        )

    return findings


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    findings = _scan_scope(tree, inherited_aliases=IMPLICIT_ALIASES)
    return sorted(set(findings))


def test_evaluation_package_does_not_mutate_process_wide_builtins() -> None:
    modules = sorted(
        path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file()
    )
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        )
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


def test_contract_rejects_direct_builtin_namespace_mutation() -> None:
    source = textwrap.dedent(
        """
        import builtins

        def probe():
            builtins.open = lambda *args, **kwargs: None
            builtins.__dict__["len"] = lambda value: 0
            builtins.__dict__.update({"abs": lambda value: value})
            vars(builtins).setdefault("round", lambda value: value)
            getattr(builtins, "__dict__").pop("sum", None)
            setattr(builtins, "max", lambda *values: values[0])
            delattr(builtins, "min")
            del builtins.__dict__["sorted"]
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "builtins.open",
        "builtins.__dict__",
        "builtins.setattr",
        "builtins.delattr",
    ):
        assert expected in findings


def test_contract_rejects_magic_and_static_module_lookup_bypasses() -> None:
    source = textwrap.dedent(
        """
        import sys

        def probe():
            __builtins__["open"] = lambda *args, **kwargs: None
            globals()["__builtins__"]["len"] = lambda value: 0
            globals().get("__builtins__").update({"abs": lambda value: value})
            sys.modules["builtins"].input = lambda prompt="": ""
            vars(sys.modules.get("builtins")).pop("sum", None)
            dict.__setitem__(__builtins__, "all", lambda values: True)
        """
    )

    findings = "\n".join(scan_source(source))
    assert "builtins.__dict__" in findings
    assert "builtins.input" in findings
    assert "builtins.dict.__setitem__" in findings


def test_contract_resolves_assignment_operator_and_inplace_aliases() -> None:
    source = textwrap.dedent(
        """
        import builtins as bi
        import operator as op

        module = bi

        def probe():
            namespace = vars(module)
            write = namespace.__setitem__
            erase = op.delitem
            module.input = lambda prompt="": ""
            write("print", lambda *args, **kwargs: None)
            erase(namespace, "enumerate")
            op.setitem(getattr(module, "__dict__"), "zip", lambda *args: ())
            namespace |= {"pow": lambda value, exponent: value}
        """
    )

    findings = "\n".join(scan_source(source))
    assert "builtins.input" in findings
    assert "builtins.__dict__.__setitem__" in findings
    assert "operator.delitem" in findings
    assert "operator.setitem" in findings
    assert "augmented assignment: builtins.__dict__" in findings


def test_contract_resolves_function_local_and_closure_aliases() -> None:
    source = textwrap.dedent(
        """
        import builtins

        def outer():
            namespace = vars(builtins)

            def inner():
                namespace["open"] = lambda *args, **kwargs: None

            return inner
        """
    )

    findings = "\n".join(scan_source(source))
    assert "builtins.__dict__" in findings


def test_contract_allows_shadowed_builtin_aliases_and_helpers() -> None:
    source = textwrap.dedent(
        """
        import builtins as bi

        def first(bi, getattr):
            bi.open = object()
            return getattr

        def second():
            bi = {}
            bi["open"] = object()
            return bi
        """
    )

    assert scan_source(source) == []


def test_contract_allows_read_only_builtin_access_and_local_mutation() -> None:
    source = textwrap.dedent(
        """
        import builtins as bi
        import sys
        from builtins import open as builtin_open

        def probe(path):
            namespace = vars(bi)
            names = sorted(namespace)
            opener = getattr(bi, "open")
            same_module = sys.modules["builtins"] is bi
            local = {}
            local["count"] = len(names)
            local.update({"callable": callable(opener)})
            with builtin_open(path, "rb") as handle:
                return local, handle.read(0), same_module
        """
    )

    assert scan_source(source) == []
