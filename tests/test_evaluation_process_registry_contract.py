from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_PROCESS_REGISTRY_CALLS = {
    "logging.basicConfig",
    "logging.captureWarnings",
    "logging.disable",
    "logging.setLoggerClass",
    "logging.shutdown",
    "warnings.catch_warnings",
    "warnings.filterwarnings",
    "warnings.resetwarnings",
    "warnings.simplefilter",
}

FORBIDDEN_LOGGER_MUTATORS = {
    "addFilter",
    "addHandler",
    "removeFilter",
    "removeHandler",
    "setLevel",
}

FORBIDDEN_LOGGER_ATTRIBUTES = {
    "disabled",
    "filters",
    "handlers",
    "level",
    "propagate",
}

CONTAINER_MUTATORS = {
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
}

TRACKED_MODULES = {"builtins", "logging", "warnings"}


def _canonical_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _canonical_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Subscript):
        parent = _canonical_name(node.value, aliases)
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return f"{parent}[{node.slice.value!r}]" if parent else None
    if isinstance(node, ast.Call):
        accessor = _canonical_name(node.func, aliases)
        if accessor == "logging.getLogger":
            return "logging.getLogger()"
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


def _name_targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in node.elts:
            names.extend(_name_targets(element))
        return names
    return []


def _is_process_logger_name(name: str | None) -> bool:
    if not name:
        return False
    return name == "logging.root" or name.startswith("logging.getLogger()")


def _is_forbidden_logger_target(name: str | None) -> bool:
    if not name:
        return False
    if _is_process_logger_name(name):
        return True
    return any(
        name == f"logging.root.{attribute}"
        or name.startswith(f"logging.getLogger().{attribute}")
        for attribute in FORBIDDEN_LOGGER_ATTRIBUTES
    )


def _aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in TRACKED_MODULES:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in TRACKED_MODULES:
                for alias in node.names:
                    if alias.name == "*":
                        continue
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

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

            resolved = _canonical_name(value, aliases)
            if not resolved:
                continue

            track = (
                resolved in FORBIDDEN_PROCESS_REGISTRY_CALLS
                or resolved in {"logging.getLogger", "logging.getLogger()", "logging.root"}
                or _is_forbidden_logger_target(resolved)
                or any(
                    resolved.endswith(f".{method}")
                    and _is_process_logger_name(resolved[: -(len(method) + 1)])
                    for method in FORBIDDEN_LOGGER_MUTATORS
                )
                or any(
                    resolved.endswith(f".{method}")
                    and _is_forbidden_logger_target(resolved[: -(len(method) + 1)])
                    for method in CONTAINER_MUTATORS
                )
            )
            if not track:
                continue

            for target in targets:
                for local in _name_targets(target):
                    if aliases.get(local) != resolved:
                        aliases[local] = resolved
                        changed = True

    return aliases


def _is_logger_mutator(target: str) -> bool:
    for method in FORBIDDEN_LOGGER_MUTATORS:
        if target.endswith(f".{method}"):
            receiver = target[: -(len(method) + 1)]
            if _is_process_logger_name(receiver):
                return True

    for method in CONTAINER_MUTATORS:
        if target.endswith(f".{method}"):
            receiver = target[: -(len(method) + 1)]
            if _is_forbidden_logger_target(receiver):
                return True

    return False


def _mutation_target_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, (ast.Attribute, ast.Subscript, ast.Name)):
        return _canonical_name(node, aliases)
    return None


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _aliases(tree)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)
            if target in FORBIDDEN_PROCESS_REGISTRY_CALLS:
                findings.append(
                    f"line {node.lineno}: process-wide registry mutation: {target}"
                )
            elif target and _is_logger_mutator(target):
                findings.append(
                    f"line {node.lineno}: process-wide logger mutation: {target}"
                )
            continue

        targets: list[ast.AST] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = list(node.targets)

        for target_node in targets:
            target = _mutation_target_name(target_node, aliases)
            if target and _is_forbidden_logger_target(target):
                findings.append(
                    f"line {node.lineno}: process-wide logger attribute mutation: {target}"
                )

    return sorted(set(findings))


def test_evaluation_package_does_not_mutate_process_logging_or_warning_registries() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        violations.extend(f"{relative}: {finding}" for finding in findings)

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import logging\nlogging.basicConfig(level=logging.INFO)\n", "logging.basicConfig"),
        ("import logging as log\nlog.disable(20)\n", "logging.disable"),
        (
            "from logging import captureWarnings as capture\ncapture(True)\n",
            "logging.captureWarnings",
        ),
        (
            "import logging\ngetattr(logging, 'setLoggerClass')(object)\n",
            "logging.setLoggerClass",
        ),
        ("import warnings\nwarnings.simplefilter('ignore')\n", "warnings.simplefilter"),
        (
            "from warnings import filterwarnings as fw\nfw('ignore')\n",
            "warnings.filterwarnings",
        ),
        (
            "import warnings\ngetattr(warnings, 'resetwarnings')()\n",
            "warnings.resetwarnings",
        ),
        (
            "import warnings\nwith warnings.catch_warnings():\n    pass\n",
            "warnings.catch_warnings",
        ),
        (
            "import logging\nlogging.getLogger().addHandler(logging.NullHandler())\n",
            "addHandler",
        ),
        (
            "import logging\nlog = logging.getLogger('gate')\nlog.setLevel(logging.INFO)\n",
            "setLevel",
        ),
        (
            "import logging\nlog = logging.getLogger('gate')\nmutate = log.removeHandler\nmutate(logging.NullHandler())\n",
            "removeHandler",
        ),
        (
            "import logging\nroot = logging.root\nroot.addFilter(object())\n",
            "addFilter",
        ),
        (
            "import logging\nlog = logging.getLogger('gate')\nlog.handlers.clear()\n",
            "handlers.clear",
        ),
        (
            "import logging\nlog = logging.getLogger('gate')\nmutate = log.filters.append\nmutate(object())\n",
            "filters.append",
        ),
        (
            "import logging\nlog = logging.getLogger('gate')\nlog.propagate = False\n",
            "propagate",
        ),
        (
            "import logging\nlogging.root.handlers = []\n",
            "handlers",
        ),
    ],
)
def test_detector_rejects_process_registry_mutation(source: str, expected: str) -> None:
    findings = "\n".join(scan_source(textwrap.dedent(source)))
    assert expected in findings


@pytest.mark.parametrize(
    "source",
    [
        "import logging\nlogger = logging.getLogger(__name__)\nlogger.info('evaluation complete')\n",
        "import logging\nname = logging.getLogger(__name__).name\n",
        "import warnings\nwarnings.warn('diagnostic', RuntimeWarning)\n",
        "import warnings\nfilters = tuple(warnings.filters)\n",
        "import logging\nhandler = logging.NullHandler()\nhandler.setLevel(logging.INFO)\n",
        "import logging\nlocal = logging.Logger('isolated')\nlocal.handlers.clear()\n",
        "class LocalLogger:\n    def addHandler(self, value):\n        return value\nLocalLogger().addHandler(object())\n",
    ],
)
def test_detector_preserves_read_only_or_local_logging_usage(source: str) -> None:
    assert scan_source(textwrap.dedent(source)) == []
