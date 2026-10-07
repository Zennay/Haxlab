from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"
WORKFLOW_PATH = (
    REPO_ROOT
    / ".github"
    / "workflows"
    / "evaluation-immutability-bypass-validation.yml"
)

FORBIDDEN_ATTRIBUTE_MUTATORS = {
    "setattr",
    "builtins.setattr",
    "delattr",
    "builtins.delattr",
    "object.__setattr__",
    "builtins.object.__setattr__",
    "object.__delattr__",
    "builtins.object.__delattr__",
    "type.__setattr__",
    "builtins.type.__setattr__",
    "type.__delattr__",
    "builtins.type.__delattr__",
}

MAPPING_MUTATING_METHODS = {
    "clear",
    "pop",
    "popitem",
    "setdefault",
    "update",
    "__setitem__",
    "__delitem__",
}

MAPPING_FUNCTION_MUTATORS = {
    "dict.clear",
    "builtins.dict.clear",
    "dict.pop",
    "builtins.dict.pop",
    "dict.popitem",
    "builtins.dict.popitem",
    "dict.setdefault",
    "builtins.dict.setdefault",
    "dict.update",
    "builtins.dict.update",
    "dict.__setitem__",
    "builtins.dict.__setitem__",
    "dict.__delitem__",
    "builtins.dict.__delitem__",
    "operator.setitem",
    "operator.delitem",
}

REFLECTION_ACCESSORS = {
    "getattr",
    "builtins.getattr",
    "vars",
    "builtins.vars",
}

ALIASABLE_ROOTS = {
    "builtins",
    "object",
    "builtins.object",
    "type",
    "builtins.type",
    "dict",
    "builtins.dict",
    "operator",
}

MAPPING_ALIAS_PREFIX = "__reflection_mapping__:"
MAPPING_MUTATOR_ALIAS_PREFIX = "__reflection_mapping_mutator__:"


def _evaluation_modules() -> list[Path]:
    return sorted(path for path in EVALUATION_ROOT.rglob("*.py") if path.is_file())


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
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


def _mapping_root(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        target = aliases.get(node.id)
        if target and target.startswith(MAPPING_ALIAS_PREFIX):
            return target[len(MAPPING_ALIAS_PREFIX):]
    if isinstance(node, ast.Attribute) and node.attr == "__dict__":
        owner = _canonical_name(node.value, aliases) or "<expression>"
        return f"{owner}.__dict__"
    if isinstance(node, ast.Call):
        target = _canonical_name(node.func, aliases)
        if target in {"vars", "builtins.vars"} and len(node.args) == 1:
            return "vars(...)"
    if isinstance(node, ast.Subscript):
        return _mapping_root(node.value, aliases)
    return None


def _bound_mapping_mutator(
    node: ast.AST | None,
    aliases: dict[str, str],
) -> str | None:
    if not isinstance(node, ast.Attribute):
        return None
    root = _mapping_root(node.value, aliases)
    if root and node.attr in MAPPING_MUTATING_METHODS:
        return f"{root}.{node.attr}"
    return None


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    tracked_roots = {"builtins", "operator"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in tracked_roots:
                    aliases[alias.asname or root] = alias.name
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.split(".", 1)[0] in tracked_roots
        ):
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue

            local = node.targets[0].id
            target = _canonical_name(node.value, aliases)
            mapping_root = _mapping_root(node.value, aliases)
            bound_mutator = _bound_mapping_mutator(node.value, aliases)

            resolved: str | None = None
            if target in (
                FORBIDDEN_ATTRIBUTE_MUTATORS
                | MAPPING_FUNCTION_MUTATORS
                | REFLECTION_ACCESSORS
                | ALIASABLE_ROOTS
            ):
                resolved = target
            elif mapping_root:
                resolved = f"{MAPPING_ALIAS_PREFIX}{mapping_root}"
            elif bound_mutator:
                resolved = f"{MAPPING_MUTATOR_ALIAS_PREFIX}{bound_mutator}"

            if resolved and aliases.get(local) != resolved:
                aliases[local] = resolved
                changed = True

    return aliases


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_ATTRIBUTE_MUTATORS:
                findings.append(
                    f"line {node.lineno}: reflective attribute mutation: {target}"
                )
                continue

            if target and target.startswith(MAPPING_MUTATOR_ALIAS_PREFIX):
                findings.append(
                    f"line {node.lineno}: immutable-object mapping mutation: "
                    f"{target[len(MAPPING_MUTATOR_ALIAS_PREFIX):]}"
                )
                continue

            if isinstance(node.func, ast.Attribute):
                root = _mapping_root(node.func.value, aliases)
                if root and node.func.attr in MAPPING_MUTATING_METHODS:
                    findings.append(
                        f"line {node.lineno}: immutable-object mapping mutation: "
                        f"{root}.{node.func.attr}"
                    )
                    continue

            if (
                target in MAPPING_FUNCTION_MUTATORS
                and node.args
                and _mapping_root(node.args[0], aliases)
            ):
                findings.append(
                    f"line {node.lineno}: immutable-object mapping mutation: {target}"
                )

        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target_node in targets:
                root = _mapping_root(target_node, aliases)
                if root:
                    findings.append(
                        f"line {node.lineno}: immutable-object mapping assignment: {root}"
                    )

        if isinstance(node, ast.Delete):
            for target_node in node.targets:
                root = _mapping_root(target_node, aliases)
                if root:
                    findings.append(
                        f"line {node.lineno}: immutable-object mapping deletion: {root}"
                    )

    return sorted(set(findings))


def test_evaluation_package_does_not_bypass_object_immutability() -> None:
    modules = _evaluation_modules()
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


def test_contract_rejects_reflective_attribute_mutators() -> None:
    source = textwrap.dedent(
        """
        class Policy:
            pass

        def probe(policy):
            setattr(policy, "threshold", 1.0)
            delattr(policy, "threshold")
            object.__setattr__(policy, "threshold", 2.0)
            object.__delattr__(policy, "threshold")
            type.__setattr__(Policy, "version", "mutated")
            type.__delattr__(Policy, "version")
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "setattr",
        "delattr",
        "object.__setattr__",
        "object.__delattr__",
        "type.__setattr__",
        "type.__delattr__",
    ):
        assert expected in findings


def test_contract_rejects_alias_and_getattr_mutation_bypasses() -> None:
    source = textwrap.dedent(
        """
        import builtins
        from builtins import setattr as mutate

        base_object = object
        force = base_object.__setattr__
        indirect = getattr(type, "__setattr__")
        read_attr = builtins.getattr
        indirect_object = read_attr(object, "__setattr__")

        def probe(policy):
            mutate(policy, "a", 1)
            force(policy, "b", 2)
            indirect(type(policy), "c", 3)
            indirect_object(policy, "d", 4)
        """
    )

    findings = "\n".join(scan_source(source))
    assert "builtins.setattr" in findings
    assert "object.__setattr__" in findings
    assert "type.__setattr__" in findings
    assert len([line for line in findings.splitlines() if "object.__setattr__" in line]) >= 2


def test_contract_rejects_assigned_getattr_accessor_chain() -> None:
    source = textwrap.dedent(
        """
        import builtins

        read_attr = builtins.getattr
        writer = read_attr(object, "__setattr__")

        def probe(policy):
            writer(policy, "threshold", 0.0)
        """
    )

    findings = scan_source(source)
    assert len(findings) == 1
    assert "object.__setattr__" in findings[0]


def test_contract_rejects_dunder_dict_and_vars_mapping_mutation() -> None:
    source = textwrap.dedent(
        """
        import operator

        def probe(policy):
            policy.__dict__["threshold"] = 1.0
            del policy.__dict__["old"]
            policy.__dict__.update({"mode": "unsafe"})
            vars(policy)["x"] = 1
            vars(policy).setdefault("y", 2)
            dict.__setitem__(vars(policy), "z", 3)
            operator.delitem(policy.__dict__, "z")
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "__dict__",
        "vars(...)",
        "update",
        "setdefault",
        "dict.__setitem__",
        "operator.delitem",
    ):
        assert expected in findings


def test_contract_rejects_stored_mapping_and_mutator_aliases() -> None:
    source = textwrap.dedent(
        """
        import operator

        def probe(policy):
            mapping = vars(policy)
            attrs = policy.__dict__
            mutate = mapping.update
            set_item = dict.__setitem__
            remove = operator.delitem

            mapping["a"] = 1
            attrs["b"] = 2
            mutate({"c": 3})
            set_item(mapping, "d", 4)
            remove(attrs, "b")
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "vars(...)",
        "__dict__",
        "update",
        "dict.__setitem__",
        "operator.delitem",
    ):
        assert expected in findings


def test_contract_rejects_vars_accessor_alias() -> None:
    source = textwrap.dedent(
        """
        import builtins

        reflect = builtins.vars

        def probe(policy):
            mapping = reflect(policy)
            mapping["threshold"] = 1.0
        """
    )

    findings = scan_source(source)
    assert findings
    assert any("vars(...)" in finding for finding in findings)


def test_contract_allows_read_only_reflection_and_immutable_replacement() -> None:
    source = textwrap.dedent(
        """
        from dataclasses import replace

        def probe(policy):
            current = getattr(policy, "threshold", None)
            exists = hasattr(policy, "threshold")
            snapshot = dict(vars(policy))
            keys = tuple(policy.__dict__.keys())
            updated = replace(policy, threshold=0.75)
            return current, exists, snapshot, keys, updated
        """
    )

    assert scan_source(source) == []


def test_exact_head_workflow_is_stale_push_safe() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert (
        "group: haxlab-evaluation-immutability-bypass-validation-${{ github.sha }}"
        in workflow
    )
    assert "cancel-in-progress: true" in workflow
    assert 'git ls-remote origin "refs/heads/${GITHUB_REF_NAME}"' in workflow
    assert 'test "$remote_head" = "${{ github.sha }}"' in workflow
