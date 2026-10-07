from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

BUILTIN_HELPERS = {"delattr", "getattr", "globals", "setattr", "vars"}
BUILTIN_TYPES = {"dict"}
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
        if node.id in BUILTIN_HELPERS | BUILTIN_TYPES:
            return f"builtins.{node.id}"
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


def _collect_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
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
        for node in ast.walk(tree):
            target: ast.Name | None = None
            value: ast.AST | None = None
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                target = node.targets[0]
                value = node.value
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.value is not None
            ):
                target = node.target
                value = node.value
            elif (
                isinstance(node, ast.NamedExpr)
                and isinstance(node.target, ast.Name)
            ):
                target = node.target
                value = node.value

            if target is None or value is None:
                continue

            resolved = _canonical_name(value, aliases)
            if resolved and aliases.get(target.id) != resolved:
                aliases[target.id] = resolved
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


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _collect_aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
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
        namespace = vars(module)
        write = namespace.__setitem__
        erase = op.delitem

        def probe():
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
