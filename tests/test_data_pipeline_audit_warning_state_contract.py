from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOTS = (
    ROOT / "src" / "haxlab" / "ingestion",
    ROOT / "src" / "haxlab" / "learning",
    ROOT / "src" / "haxlab" / "runtime",
)
WARNING_MUTATORS = {
    "warnings.filterwarnings",
    "warnings.simplefilter",
    "warnings.resetwarnings",
    "warnings.catch_warnings",
    "warnings._filters_mutated",
}
WARNING_STATE = {
    "warnings.filters",
    "warnings.defaultaction",
    "warnings.onceregistry",
    "warnings._onceregistry",
    "warnings._defaultaction",
    "warnings.showwarning",
    "warnings.formatwarning",
}
STATE_MUTATOR_METHODS = {
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
    "__delitem__",
    "__setitem__",
}
FUNCTIONAL_MUTATORS = {
    "operator.delitem",
    "operator.iadd",
    "operator.iconcat",
    "operator.ior",
    "operator.setitem",
    "list.__delitem__",
    "list.__iadd__",
    "list.__imul__",
    "list.__setitem__",
    "list.append",
    "list.clear",
    "list.extend",
    "list.insert",
    "list.pop",
    "list.remove",
    "list.reverse",
    "list.sort",
}


def _audit_paths() -> list[Path]:
    return sorted(
        {
            path
            for root in AUDIT_ROOTS
            if root.exists()
            for path in root.rglob("*_audit.py")
            if path.is_file()
        },
        key=lambda path: path.as_posix(),
    )


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


def _simple_assignment(node: ast.AST) -> tuple[str, ast.AST] | None:
    if (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ):
        return node.targets[0].id, node.value
    if (
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        return node.target.id, node.value
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return node.target.id, node.value
    return None


def _tracked_alias(name: str | None) -> bool:
    if not name:
        return False
    if name in WARNING_MUTATORS or name in WARNING_STATE:
        return True
    if name in FUNCTIONAL_MUTATORS:
        return True
    return any(
        name == f"{state}.{method}"
        for state in WARNING_STATE
        for method in STATE_MUTATOR_METHODS
    )


def _aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                local = item.asname or item.name.split(".", 1)[0]
                aliases[local] = (
                    item.name if item.asname else item.name.split(".", 1)[0]
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for item in node.names:
                if item.name == "*":
                    continue
                local = item.asname or item.name
                aliases[local] = f"{module}.{item.name}" if module else item.name

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            assignment = _simple_assignment(node)
            if assignment is None:
                continue
            local, expression = assignment
            target = _canonical_name(expression, aliases)
            if _tracked_alias(target) and aliases.get(local) != target:
                aliases[local] = target
                changed = True

    return aliases


def _state_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Subscript):
        return _state_name(node.value, aliases)
    name = _canonical_name(node, aliases)
    return name if name in WARNING_STATE else None


def _direct_mutation_target_name(
    node: ast.AST | None,
    aliases: dict[str, str],
) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Subscript):
        return _state_name(node.value, aliases)
    if isinstance(node, ast.Attribute):
        name = _canonical_name(node, aliases)
        return name if name in WARNING_STATE else None
    return None


def _inplace_mutation_target_name(
    node: ast.AST | None,
    aliases: dict[str, str],
) -> str | None:
    direct = _direct_mutation_target_name(node, aliases)
    if direct:
        return direct
    if isinstance(node, ast.Name):
        name = aliases.get(node.id)
        return name if name in WARNING_STATE else None
    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "warnings":
            if any(item.name == "*" for item in node.names):
                findings.append(
                    f"line {node.lineno}: wildcard warnings import exposes "
                    "process-global policy mutators"
                )

        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in WARNING_MUTATORS:
                findings.append(
                    f"line {node.lineno}: warning policy mutation call: {target}"
                )
                continue

            if target in FUNCTIONAL_MUTATORS and node.args:
                state = _state_name(node.args[0], aliases)
                if state:
                    findings.append(
                        f"line {node.lineno}: warning state functional mutation: "
                        f"{target}({state}, ...)"
                    )
                    continue

            if target in {"setattr", "builtins.setattr", "delattr", "builtins.delattr"}:
                if (
                    len(node.args) >= 2
                    and _canonical_name(node.args[0], aliases) == "warnings"
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                    and f"warnings.{node.args[1].value}" in WARNING_STATE
                ):
                    findings.append(
                        f"line {node.lineno}: reflected warning state mutation: "
                        f"{target}(warnings, {node.args[1].value!r}, ...)"
                    )
                    continue

            if target:
                for state in WARNING_STATE:
                    prefix = f"{state}."
                    if target.startswith(prefix):
                        method = target[len(prefix):]
                        if method in STATE_MUTATOR_METHODS:
                            findings.append(
                                f"line {node.lineno}: warning state mutation call: "
                                f"{target}"
                            )
                            break

        if isinstance(node, ast.Assign):
            for target in node.targets:
                state = _direct_mutation_target_name(target, aliases)
                if state:
                    findings.append(
                        f"line {node.lineno}: warning state assignment: {state}"
                    )
        elif isinstance(node, ast.AnnAssign):
            state = _direct_mutation_target_name(node.target, aliases)
            if state:
                findings.append(
                    f"line {node.lineno}: warning state assignment: {state}"
                )
        elif isinstance(node, ast.AugAssign):
            state = _inplace_mutation_target_name(node.target, aliases)
            if state:
                findings.append(
                    f"line {node.lineno}: warning state augmented mutation: {state}"
                )
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                state = _direct_mutation_target_name(target, aliases)
                if state:
                    findings.append(
                        f"line {node.lineno}: warning state deletion: {state}"
                    )

    return sorted(set(findings))


def test_data_pipeline_auditors_do_not_mutate_warning_policy_state() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one data-pipeline *_audit.py module"

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = scan_source(path.read_text(encoding="utf-8"), filename=relative)
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import warnings\nwarnings.filterwarnings('ignore')\n", "filterwarnings"),
        (
            "from warnings import simplefilter as sf\nsf('ignore')\n",
            "simplefilter",
        ),
        (
            "import warnings\nmutate = warnings.resetwarnings\nmutate()\n",
            "resetwarnings",
        ),
        (
            "import warnings\ngetattr(warnings, 'filterwarnings')('ignore')\n",
            "filterwarnings",
        ),
        (
            "from warnings import catch_warnings\n"
            "with catch_warnings():\n"
            "    pass\n",
            "catch_warnings",
        ),
        (
            "import warnings\nwarnings.filters.clear()\n",
            "warnings.filters.clear",
        ),
        (
            "import warnings\nfilters = warnings.filters\nfilters.append(('x',))\n",
            "warnings.filters.append",
        ),
        (
            "import warnings\nwarnings.filters[0] = ('x',)\n",
            "warning state assignment",
        ),
        (
            "import warnings\ndel warnings.filters[:]\n",
            "warning state deletion",
        ),
        (
            "import warnings\nwarnings.filters += []\n",
            "augmented mutation",
        ),
        (
            "import warnings\nwarnings.defaultaction = 'ignore'\n",
            "warnings.defaultaction",
        ),
        (
            "import warnings\nsetattr(warnings, 'onceregistry', {})\n",
            "reflected warning state mutation",
        ),
        (
            "import warnings\nwarnings.showwarning = lambda *args: None\n",
            "warnings.showwarning",
        ),
        (
            "import warnings\nlist.clear(warnings.filters)\n",
            "list.clear",
        ),
        (
            "import warnings\nwarnings._filters_mutated()\n",
            "_filters_mutated",
        ),
        (
            "import operator as op\nimport warnings\n"
            "op.setitem(warnings.filters, 0, ('x',))\n",
            "operator.setitem",
        ),
        (
            "from warnings import *\n",
            "wildcard warnings import",
        ),
    ],
)
def test_contract_rejects_warning_policy_mutation(
    source: str,
    expected: str,
) -> None:
    findings = scan_source(source)
    assert any(expected in finding for finding in findings), findings


def test_contract_allows_warning_emission_and_read_only_inspection() -> None:
    source = """
import warnings

def audit(value: object) -> tuple[int, str]:
    if value is None:
        warnings.warn("missing optional evidence", RuntimeWarning)
    policy = warnings.filters
    snapshot = tuple(policy)
    action = warnings.defaultaction
    local = list(snapshot)
    local.clear()
    return len(snapshot), action
"""
    assert scan_source(source) == []
