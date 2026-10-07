from __future__ import annotations

import ast
import textwrap
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

FORBIDDEN_CALLS = {
    "logging.basicConfig",
    "logging.captureWarnings",
    "logging.disable",
    "logging.shutdown",
    "logging.config.dictConfig",
    "logging.config.fileConfig",
    "logging.config.listen",
    "logging.config.stopListening",
    "warnings.filterwarnings",
    "warnings.resetwarnings",
    "warnings.simplefilter",
}

LOGGER_MUTATORS = {
    "addFilter",
    "addHandler",
    "removeFilter",
    "removeHandler",
    "setLevel",
}

LOGGER_MUTABLE_ATTRIBUTES = {
    "disabled",
    "filters",
    "handlers",
    "level",
    "parent",
    "propagate",
}

WARNINGS_MUTATORS = {
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
    "__delitem__",
    "__iadd__",
    "__imul__",
    "__setitem__",
}

TRACKED_IMPORT_ROOTS = {"builtins", "logging", "warnings"}


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


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in TRACKED_IMPORT_ROOTS:
                    aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root not in TRACKED_IMPORT_ROOTS:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _is_logger_expression(node: ast.AST | None, aliases: dict[str, str], logger_aliases: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in logger_aliases
    target = _canonical_name(node, aliases)
    if target in {"logging.root", "logging.Logger.root"}:
        return True
    if isinstance(node, ast.Call):
        return _canonical_name(node.func, aliases) == "logging.getLogger"
    return False


def _is_warnings_filters(node: ast.AST | None, aliases: dict[str, str], warnings_aliases: set[str]) -> bool:
    if node is None:
        return False
    if isinstance(node, ast.Name) and node.id in warnings_aliases:
        return True
    return _canonical_name(node, aliases) == "warnings.filters"


def _resolve_state_aliases(tree: ast.AST, aliases: dict[str, str]) -> tuple[set[str], set[str], dict[str, str]]:
    logger_aliases: set[str] = set()
    warnings_aliases: set[str] = set()
    callable_aliases: dict[str, str] = {}

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

            if value is None:
                continue

            target_name = _canonical_name(value, aliases)
            is_logger = _is_logger_expression(value, aliases, logger_aliases)
            is_filters = _is_warnings_filters(value, aliases, warnings_aliases)

            callable_target: str | None = None
            if target_name in FORBIDDEN_CALLS:
                callable_target = target_name
            elif isinstance(value, ast.Attribute) and value.attr in LOGGER_MUTATORS:
                if _is_logger_expression(value.value, aliases, logger_aliases):
                    callable_target = f"logger.{value.attr}"
            elif isinstance(value, ast.Attribute) and value.attr in WARNINGS_MUTATORS:
                if _is_warnings_filters(value.value, aliases, warnings_aliases):
                    callable_target = f"warnings.filters.{value.attr}"
            elif isinstance(value, ast.Call):
                resolved = _canonical_name(value, aliases)
                if resolved in FORBIDDEN_CALLS:
                    callable_target = resolved

            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                if is_logger and name not in logger_aliases:
                    logger_aliases.add(name)
                    changed = True
                if is_filters and name not in warnings_aliases:
                    warnings_aliases.add(name)
                    changed = True
                if callable_target and callable_aliases.get(name) != callable_target:
                    callable_aliases[name] = callable_target
                    changed = True

    return logger_aliases, warnings_aliases, callable_aliases


def _assignment_targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        return [node.target]
    return []


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    aliases = _import_aliases(tree)
    logger_aliases, warnings_aliases, callable_aliases = _resolve_state_aliases(tree, aliases)
    findings: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            target = _canonical_name(node.func, aliases)

            if isinstance(node.func, ast.Name) and node.func.id in callable_aliases:
                findings.append(
                    f"line {node.lineno}: global diagnostic registry mutation: "
                    f"{callable_aliases[node.func.id]}"
                )
                continue

            if target in FORBIDDEN_CALLS:
                findings.append(
                    f"line {node.lineno}: global diagnostic registry mutation: {target}"
                )
                continue

            if isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr in LOGGER_MUTATORS
                    and _is_logger_expression(node.func.value, aliases, logger_aliases)
                ):
                    findings.append(
                        f"line {node.lineno}: logger registry mutation: {node.func.attr}"
                    )
                    continue
                if (
                    node.func.attr in WARNINGS_MUTATORS
                    and _is_warnings_filters(node.func.value, aliases, warnings_aliases)
                ):
                    findings.append(
                        f"line {node.lineno}: warnings.filters mutation: {node.func.attr}"
                    )
                    continue

            if (
                target
                and target.startswith("logging.getLogger().")
                and target.rsplit(".", 1)[-1] in LOGGER_MUTATORS
            ):
                findings.append(
                    f"line {node.lineno}: logger registry mutation: "
                    f"{target.rsplit('.', 1)[-1]}"
                )
                continue

        for target in _assignment_targets(node):
            if isinstance(target, ast.Attribute):
                if (
                    target.attr in LOGGER_MUTABLE_ATTRIBUTES
                    and _is_logger_expression(target.value, aliases, logger_aliases)
                ):
                    findings.append(
                        f"line {node.lineno}: logger registry assignment: {target.attr}"
                    )
            elif isinstance(target, ast.Subscript):
                if _is_warnings_filters(target.value, aliases, warnings_aliases):
                    findings.append(
                        f"line {node.lineno}: warnings.filters assignment"
                    )

        if isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    if (
                        target.attr in LOGGER_MUTABLE_ATTRIBUTES
                        and _is_logger_expression(target.value, aliases, logger_aliases)
                    ):
                        findings.append(
                            f"line {node.lineno}: logger registry deletion: {target.attr}"
                        )
                elif isinstance(target, ast.Subscript):
                    if _is_warnings_filters(target.value, aliases, warnings_aliases):
                        findings.append(
                            f"line {node.lineno}: warnings.filters deletion"
                        )

    return sorted(set(findings))


def test_evaluation_package_does_not_mutate_global_logging_or_warnings_state() -> None:
    modules = _evaluation_modules()
    assert modules, "expected evaluation Python modules"

    failures: dict[str, list[str]] = {}
    for path in modules:
        relative = path.relative_to(REPO_ROOT)
        findings = scan_source(path.read_text(encoding="utf-8"), filename=str(relative))
        if findings:
            failures[str(relative)] = findings

    assert failures == {}


def test_contract_rejects_logging_registry_configuration() -> None:
    source = textwrap.dedent(
        """
        import logging
        import logging.config as log_config

        def probe():
            logging.basicConfig(level=logging.INFO)
            logging.disable(logging.CRITICAL)
            logging.captureWarnings(True)
            logging.shutdown()
            log_config.dictConfig({"version": 1})
            log_config.fileConfig("logging.ini")
            log_config.listen()
            log_config.stopListening()
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "logging.basicConfig",
        "logging.disable",
        "logging.captureWarnings",
        "logging.shutdown",
        "logging.config.dictConfig",
        "logging.config.fileConfig",
        "logging.config.listen",
        "logging.config.stopListening",
    ):
        assert expected in findings


def test_contract_rejects_named_and_root_logger_mutation() -> None:
    source = textwrap.dedent(
        """
        import logging

        logger = logging.getLogger("gate")
        alias = logger

        def probe(handler, filt):
            logger.setLevel(logging.DEBUG)
            alias.addHandler(handler)
            alias.removeHandler(handler)
            logger.addFilter(filt)
            logger.removeFilter(filt)
            logger.propagate = False
            logger.disabled = True
            logging.getLogger().setLevel(logging.ERROR)
            logging.root.handlers = []
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "setLevel",
        "addHandler",
        "removeHandler",
        "addFilter",
        "removeFilter",
        "propagate",
        "disabled",
        "handlers",
    ):
        assert expected in findings


def test_contract_rejects_warnings_registry_mutation() -> None:
    source = textwrap.dedent(
        """
        import warnings

        filters = warnings.filters

        def probe():
            warnings.simplefilter("error")
            warnings.filterwarnings("ignore")
            warnings.resetwarnings()
            warnings.filters.append(("ignore", None, Warning, None, 0))
            filters.clear()
            filters[:] = []
            del filters[:]
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "warnings.simplefilter",
        "warnings.filterwarnings",
        "warnings.resetwarnings",
        "warnings.filters mutation: append",
        "warnings.filters mutation: clear",
        "warnings.filters assignment",
        "warnings.filters deletion",
    ):
        assert expected in findings


def test_contract_rejects_alias_and_constant_getattr_bypasses() -> None:
    source = textwrap.dedent(
        """
        import logging as log
        import warnings as warn
        from builtins import getattr as read_attr
        from logging import basicConfig as configure
        from warnings import simplefilter as configure_warnings

        logger = log.getLogger("gate")
        mutate_level = logger.setLevel
        mutate_filters = warn.filters.append

        def probe():
            configure()
            configure_warnings("error")
            mutate_level(log.ERROR)
            mutate_filters(("ignore", None, Warning, None, 0))
            read_attr(log, "disable")(log.CRITICAL)
            read_attr(log.getLogger(), "addHandler")(object())
            read_attr(warn, "filters").clear()
        """
    )

    findings = "\n".join(scan_source(source))
    for expected in (
        "logging.basicConfig",
        "warnings.simplefilter",
        "logger.setLevel",
        "warnings.filters.append",
        "logging.disable",
        "addHandler",
        "warnings.filters mutation: clear",
    ):
        assert expected in findings


def test_contract_allows_read_only_diagnostics_and_ephemeral_warning_context() -> None:
    source = textwrap.dedent(
        """
        import logging
        import warnings

        logger = logging.getLogger(__name__)

        def probe():
            logger.info("evaluation started")
            level = logger.getEffectiveLevel()
            enabled = logger.isEnabledFor(logging.INFO)
            with warnings.catch_warnings(record=True) as caught:
                warnings.warn("local probe", RuntimeWarning)
            return level, enabled, len(caught), tuple(warnings.filters)
        """
    )

    assert scan_source(source) == []
