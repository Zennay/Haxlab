from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "src" / "haxlab"

_MODULE_MUTATORS = {
    "logging.addLevelName",
    "logging.basicConfig",
    "logging.captureWarnings",
    "logging.disable",
    "logging.setLoggerClass",
    "logging.setLogRecordFactory",
    "logging.shutdown",
    "logging.config.dictConfig",
    "logging.config.fileConfig",
    "logging.config.listen",
    "logging.config.stopListening",
}
_LOGGER_MUTATORS = {
    "addFilter",
    "addHandler",
    "removeFilter",
    "removeHandler",
    "setLevel",
}
_LOGGER_MUTABLE_ATTRIBUTES = {
    "disabled",
    "filters",
    "handlers",
    "level",
    "parent",
    "propagate",
}
_CONTAINER_MUTATORS = {
    "__delitem__",
    "__iadd__",
    "__imul__",
    "__setitem__",
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
}
_MODULE_MUTABLE_ATTRIBUTES = {
    "lastResort",
    "logAsyncioTasks",
    "logMultiprocessing",
    "logProcesses",
    "logThreads",
    "raiseExceptions",
    "root",
}


def _qualname(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualname(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    if isinstance(node, ast.Call):
        callable_name = _qualname(node.func, aliases)
        if callable_name in {"getattr", "builtins.getattr"}:
            if (
                len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
            ):
                parent = _qualname(node.args[0], aliases)
                if parent is not None:
                    return f"{parent}.{node.args[1].value}"
            return None
        if callable_name == "logging.getLogger":
            return "logging.Logger"
    return None


def _logger_member(name: str | None) -> tuple[str, str] | None:
    if name is None:
        return None

    for prefix in ("logging.Logger.", "logging.root."):
        if not name.startswith(prefix):
            continue
        suffix = name[len(prefix):]
        if "." in suffix:
            owner, member = suffix.rsplit(".", 1)
            return owner, member
        return "", suffix
    return None


def _logging_mutation_reason(name: str | None) -> str | None:
    if name is None:
        return None
    if name in _MODULE_MUTATORS:
        return f"module_mutator:{name}"

    logger_member = _logger_member(name)
    if logger_member is None:
        return None

    owner, member = logger_member
    if owner == "" and member in _LOGGER_MUTATORS:
        return f"logger_mutator:{member}"
    if owner in _LOGGER_MUTABLE_ATTRIBUTES and member in _CONTAINER_MUTATORS:
        return f"logger_container_mutator:{owner}.{member}"
    return None


def _logging_target_reason(name: str | None) -> str | None:
    if name is None:
        return None

    if name.startswith("logging.") and not name.startswith("logging.Logger."):
        suffix = name[len("logging."):]
        if "." not in suffix and suffix in _MODULE_MUTABLE_ATTRIBUTES:
            return f"module_attribute:{suffix}"
        if name.startswith("logging.root."):
            suffix = name[len("logging.root."):]
            if "." not in suffix and suffix in _LOGGER_MUTABLE_ATTRIBUTES:
                return f"logger_attribute:{suffix}"

    logger_member = _logger_member(name)
    if logger_member is not None:
        owner, member = logger_member
        if owner == "" and member in _LOGGER_MUTABLE_ATTRIBUTES:
            return f"logger_attribute:{member}"
    return None


class _LoggingStateVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.aliases: dict[str, str] = {}
        self.violations: list[str] = []

    def _record(self, node: ast.AST, reason: str | None) -> None:
        if reason is not None:
            self.violations.append(f"{node.lineno}:{reason}")

    def _dynamic_logging_getattr_reason(self, node: ast.Call) -> str | None:
        accessor = _qualname(node.func, self.aliases)
        if accessor not in {"getattr", "builtins.getattr"} or len(node.args) < 2:
            return None
        member = node.args[1]
        if isinstance(member, ast.Constant) and isinstance(member.value, str):
            return None
        owner = _qualname(node.args[0], self.aliases)
        if owner in {"logging", "logging.root", "logging.Logger"}:
            return f"dynamic_logging_getattr:{owner}"
        return None

    def _bind_name(self, target: ast.AST, source_name: str | None) -> None:
        if isinstance(target, ast.Name):
            if source_name is None:
                self.aliases.pop(target.id, None)
            else:
                self.aliases[target.id] = source_name
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_name(element, None)

    def _bind_assignment(self, target: ast.AST, value: ast.AST) -> None:
        if (
            isinstance(target, (ast.Tuple, ast.List))
            and isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
        ):
            for target_item, value_item in zip(target.elts, value.elts, strict=True):
                self._bind_assignment(target_item, value_item)
            return
        self._bind_name(target, _qualname(value, self.aliases))

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            local = item.asname or item.name.split(".", 1)[0]
            target = item.name if item.asname else item.name.split(".", 1)[0]
            self.aliases[local] = target
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for item in node.names:
            if item.name == "*" and module in {"logging", "logging.config"}:
                self.violations.append(
                    f"{node.lineno}:wildcard_logging_import:{module}"
                )
                continue
            if item.name == "*":
                continue
            local = item.asname or item.name
            self.aliases[local] = f"{module}.{item.name}" if module else item.name
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._record(
                target,
                _logging_target_reason(_qualname(target, self.aliases)),
            )
        for target in node.targets:
            self._bind_assignment(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._record(
            node.target,
            _logging_target_reason(_qualname(node.target, self.aliases)),
        )
        if node.value is None:
            self._bind_name(node.target, None)
        else:
            self._bind_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._record(
            node.target,
            _logging_target_reason(_qualname(node.target, self.aliases)),
        )
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        for target in node.targets:
            self._record(
                target,
                _logging_target_reason(_qualname(target, self.aliases)),
            )
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_assignment(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self._record(node, self._dynamic_logging_getattr_reason(node))
        self._record(
            node,
            _logging_mutation_reason(_qualname(node.func, self.aliases)),
        )
        self.generic_visit(node)


def _logging_state_violations(
    source: str,
    *,
    filename: str = "<contract>",
) -> list[str]:
    tree = ast.parse(source, filename=filename)
    visitor = _LoggingStateVisitor()
    visitor.visit(tree)
    return sorted(set(visitor.violations))


def _audit_paths() -> list[Path]:
    return sorted(
        (
            path
            for path in AUDIT_ROOT.rglob("*_audit.py")
            if path.is_file()
        ),
        key=lambda path: path.as_posix(),
    )


def test_data_pipeline_auditors_do_not_mutate_logging_state() -> None:
    paths = _audit_paths()
    assert paths, "expected at least one HaxLab data-pipeline *_audit.py module"
    assert ROOT / "src" / "haxlab" / "skill" / "leaderboard_audit.py" in paths

    failures: dict[str, list[str]] = {}
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        findings = _logging_state_violations(
            path.read_text(encoding="utf-8"),
            filename=relative,
        )
        if findings:
            failures[relative] = findings

    assert failures == {}


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        ("import logging\nlogging.basicConfig(level=20)", "module_mutator"),
        ("from logging import disable as d\nd(50)", "module_mutator"),
        ("import logging\nlogging.setLoggerClass(object)", "module_mutator"),
        (
            "import logging\nfactory = logging.setLogRecordFactory\nfactory(object())",
            "module_mutator",
        ),
        ("import logging\ngetattr(logging, 'captureWarnings')(True)", "module_mutator"),
        ("import logging\nlogging.addLevelName(15, 'TRACE')", "module_mutator"),
        ("import logging.config\nlogging.config.dictConfig({})", "module_mutator"),
        (
            "import logging.config as lc\nconfigure = getattr(lc, 'fileConfig')\n"
            "configure('logging.ini')",
            "module_mutator",
        ),
    ],
)
def test_contract_rejects_process_global_logging_mutators(
    source: str,
    reason: str,
) -> None:
    findings = _logging_state_violations(source)
    assert any(reason in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("source", "reason"),
    [
        (
            "import logging\nlogger = logging.getLogger('audit')\nlogger.setLevel(10)",
            "logger_mutator",
        ),
        (
            "from logging import getLogger as gl\nlogger = gl('audit')\n"
            "add = logger.addHandler\nadd(object())",
            "logger_mutator",
        ),
        (
            "import logging\ngetattr(logging.getLogger(), 'removeFilter')(object())",
            "logger_mutator",
        ),
        (
            "import logging\nlogging.root.addHandler(object())",
            "logger_mutator",
        ),
        (
            "import logging\nlogger = logging.getLogger()\nlogger.handlers.append(object())",
            "logger_container_mutator",
        ),
        (
            "import logging\nlogger = logging.getLogger()\nlogger.disabled = True",
            "logger_attribute",
        ),
        (
            "import logging\nlogging.raiseExceptions = False",
            "module_attribute",
        ),
    ],
)
def test_contract_rejects_logger_configuration_mutation(
    source: str,
    reason: str,
) -> None:
    findings = _logging_state_violations(source)
    assert any(reason in finding for finding in findings), findings


@pytest.mark.parametrize(
    "source",
    [
        "import logging\nname = 'disable'\ngetattr(logging, name)(50)",
        (
            "import logging\nlogger = logging.getLogger('audit')\n"
            "name = 'setLevel'\ngetattr(logger, name)(10)"
        ),
        (
            "from builtins import getattr as ga\nimport logging\n"
            "logger = logging.getLogger('audit')\nname = 'handlers'\n"
            "handlers = ga(logger, name)\nhandlers.clear()"
        ),
    ],
)
def test_contract_fails_closed_on_dynamic_logging_getattr(source: str) -> None:
    findings = _logging_state_violations(source)
    assert any("dynamic_logging_getattr" in finding for finding in findings), findings


def test_contract_rejects_wildcard_logging_imports() -> None:
    findings = _logging_state_violations("from logging import *\n")
    assert any("wildcard_logging_import" in finding for finding in findings)


def test_contract_preserves_logging_emission_and_read_only_inspection() -> None:
    source = """
import logging
from logging import getLogger

logger = getLogger("haxlab.audit")
logger.debug("debug evidence")
logger.info("info evidence")
logger.warning("warning evidence")
logger.error("error evidence")
logger.exception("exception evidence")
logger.critical("critical evidence")
enabled = logger.isEnabledFor(logging.INFO)
effective = logger.getEffectiveLevel()
handler_count = len(logger.handlers)
root_level = logging.root.level
factory = logging.getLogRecordFactory()
logger_class = logging.getLoggerClass()
constant_level = getattr(logger, "level")
"""
    assert _logging_state_violations(source) == []
