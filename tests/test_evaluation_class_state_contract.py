from __future__ import annotations

import ast
from pathlib import Path
import textwrap

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = REPO_ROOT / "src" / "haxlab" / "evaluation"

MUTABLE_CONSTRUCTORS = {
    "array.array",
    "bytearray",
    "builtins.bytearray",
    "builtins.dict",
    "builtins.list",
    "builtins.set",
    "collections.ChainMap",
    "collections.Counter",
    "collections.OrderedDict",
    "collections.UserDict",
    "collections.UserList",
    "collections.defaultdict",
    "collections.deque",
    "dict",
    "dict.fromkeys",
    "io.BytesIO",
    "io.StringIO",
    "list",
    "queue.LifoQueue",
    "queue.PriorityQueue",
    "queue.Queue",
    "queue.SimpleQueue",
    "set",
    "types.SimpleNamespace",
    "weakref.WeakKeyDictionary",
    "weakref.WeakValueDictionary",
}


def _record_import(node: ast.Import, aliases: dict[str, str]) -> None:
    for alias in node.names:
        local = alias.asname or alias.name.split(".", 1)[0]
        aliases[local] = alias.name


def _record_import_from(node: ast.ImportFrom, aliases: dict[str, str]) -> None:
    if node.module is None:
        return
    for alias in node.names:
        if alias.name == "*":
            continue
        aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"


def _qualified_name(node: ast.AST | None, aliases: dict[str, str]) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return None if parent is None else f"{parent}.{node.attr}"
    if isinstance(node, ast.Call):
        accessor = _qualified_name(node.func, aliases)
        if (
            accessor in {"getattr", "builtins.getattr"}
            and len(node.args) == 2
            and not node.keywords
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            owner = _qualified_name(node.args[0], aliases)
            if owner is not None:
                return f"{owner}.{node.args[1].value}"
    return None


def _record_alias_targets(
    targets: list[ast.expr],
    value: ast.expr,
    aliases: dict[str, str],
) -> None:
    qualified = _qualified_name(value, aliases)
    if qualified is None:
        return
    for target in targets:
        if isinstance(target, ast.Name):
            aliases[target.id] = qualified


def _is_mutable_value(node: ast.expr, aliases: dict[str, str]) -> bool:
    if isinstance(
        node,
        (
            ast.List,
            ast.Dict,
            ast.Set,
            ast.ListComp,
            ast.DictComp,
            ast.SetComp,
        ),
    ):
        return True
    if isinstance(node, ast.Tuple):
        return any(_is_mutable_value(element, aliases) for element in node.elts)
    if isinstance(node, ast.Call):
        return _qualified_name(node.func, aliases) in MUTABLE_CONSTRUCTORS
    return False


def _target_names(target: ast.expr) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for element in target.elts:
            names.extend(_target_names(element))
        return names
    return ["<attribute>"]


class SharedClassStateScanner:
    def __init__(self) -> None:
        self.violations: list[str] = []

    def scan(self, tree: ast.Module) -> None:
        aliases: dict[str, str] = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                _record_import(statement, aliases)
            elif isinstance(statement, ast.ImportFrom):
                _record_import_from(statement, aliases)
            elif isinstance(statement, ast.Assign):
                _record_alias_targets(statement.targets, statement.value, aliases)
            elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
                _record_alias_targets([statement.target], statement.value, aliases)

        for statement in tree.body:
            if isinstance(statement, ast.ClassDef):
                self._scan_class(statement, aliases)

    def _scan_class(
        self,
        node: ast.ClassDef,
        inherited_aliases: dict[str, str],
    ) -> None:
        aliases = dict(inherited_aliases)
        for statement in node.body:
            if isinstance(statement, ast.Import):
                _record_import(statement, aliases)
                continue
            if isinstance(statement, ast.ImportFrom):
                _record_import_from(statement, aliases)
                continue
            if isinstance(statement, ast.Assign):
                if _is_mutable_value(statement.value, aliases):
                    names = [
                        name
                        for target in statement.targets
                        for name in _target_names(target)
                    ]
                    self.violations.append(
                        f"line {statement.lineno}: class {node.name} has shared "
                        f"mutable state: {','.join(names)}"
                    )
                _record_alias_targets(statement.targets, statement.value, aliases)
                continue
            if isinstance(statement, ast.AnnAssign) and statement.value is not None:
                if _is_mutable_value(statement.value, aliases):
                    self.violations.append(
                        f"line {statement.lineno}: class {node.name} has shared "
                        f"mutable state: {','.join(_target_names(statement.target))}"
                    )
                _record_alias_targets([statement.target], statement.value, aliases)
                continue
            if isinstance(statement, ast.ClassDef):
                self._scan_class(statement, aliases)


def scan_source(source: str, *, filename: str = "<memory>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    scanner = SharedClassStateScanner()
    scanner.scan(tree)
    return sorted(set(scanner.violations))


def test_evaluation_package_has_no_shared_mutable_class_state() -> None:
    violations: list[str] = []

    for path in sorted(EVALUATION_ROOT.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT)
        for violation in scan_source(
            path.read_text(encoding="utf-8"),
            filename=str(relative),
        ):
            violations.append(f"{relative}: {violation}")

    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "class Gate:\n    cache = {}\n",
        "class Gate:\n    values: list[int] = []\n",
        "class Gate:\n    seen = set()\n",
        "class Gate:\n    left = right = []\n",
        "class Gate:\n    state = ({},)\n",
        "import collections as c\nclass Gate:\n    cache = c.defaultdict(list)\n",
        "from collections import deque as Queue\nclass Gate:\n    pending = Queue()\n",
        "Factory = dict\nclass Gate:\n    cache = Factory()\n",
        "import collections\nFactory = getattr(collections, 'deque')\nclass Gate:\n    pending = Factory()\n",
        "class Outer:\n    Factory = list\n    cache = Factory()\n    class Inner:\n        seen = {}\n",
        "from collections import Counter\nclass Gate:\n    counts = Counter()\n",
        "import collections as c\nclass Gate:\n    layers = c.ChainMap()\n",
        "from collections import UserDict as Bag\nclass Gate:\n    state = Bag()\n",
        "import array\nclass Gate:\n    values = array.array('I')\n",
        "from io import BytesIO\nclass Gate:\n    buffer = BytesIO()\n",
        "import queue\nclass Gate:\n    pending = queue.SimpleQueue()\n",
        "from types import SimpleNamespace\nclass Gate:\n    state = SimpleNamespace()\n",
        "import weakref\nclass Gate:\n    cache = weakref.WeakKeyDictionary()\n",
    ],
)
def test_detector_rejects_shared_mutable_class_state(source: str) -> None:
    assert scan_source(textwrap.dedent(source)), source


@pytest.mark.parametrize(
    "source",
    [
        "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Policy:\n    roles: tuple[str, ...] = ()\n",
        "from dataclasses import dataclass, field\n@dataclass\nclass Evidence:\n    rows: list[str] = field(default_factory=list)\n",
        "class Gate:\n    roles = ('gk', 'dm', 'am', 'st')\n",
        "class Gate:\n    def evaluate(self, value):\n        local_cache = {}\n        return local_cache.setdefault(value, value)\n",
        "from types import MappingProxyType\nclass Gate:\n    constants = MappingProxyType({'a': 1})\n",
        "class Gate:\n    limit: int = 500\n    enabled: bool = False\n",
    ],
)
def test_detector_allows_instance_factories_and_immutable_class_values(
    source: str,
) -> None:
    assert scan_source(textwrap.dedent(source)) == []
