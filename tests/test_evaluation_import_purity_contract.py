from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "src" / "haxlab" / "evaluation"


def _evaluation_modules() -> list[str]:
    modules: list[str] = []
    for path in sorted(EVALUATION_DIR.rglob("*.py")):
        relative = path.relative_to(EVALUATION_DIR)
        parts = list(relative.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        suffix = ".".join(parts)
        modules.append("haxlab.evaluation" + (f".{suffix}" if suffix else ""))
    return sorted(set(modules))


_IMPORT_PROBE = r"""
import importlib
import os
import sys

module_name = sys.argv[1]

WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_RDWR
    | os.O_CREAT
    | os.O_TRUNC
    | os.O_APPEND
)

MUTATING_EVENTS = {
    "os.chdir",
    "os.chmod",
    "os.chown",
    "os.link",
    "os.mkdir",
    "os.remove",
    "os.rename",
    "os.replace",
    "os.rmdir",
    "os.symlink",
    "os.truncate",
    "os.unlink",
    "os.utime",
    "os.setxattr",
    "os.removexattr",
}


def _audit(event, args):
    if event == "open":
        _path, mode, flags = args
        if isinstance(mode, str) and any(marker in mode for marker in ("w", "a", "x", "+")):
            raise RuntimeError(f"filesystem write during import: {event}:{mode}")
        if isinstance(flags, int) and flags & WRITE_FLAGS:
            raise RuntimeError(f"filesystem write during import: {event}:flags={flags}")
    if event in MUTATING_EVENTS:
        raise RuntimeError(f"filesystem mutation during import: {event}")


sys.addaudithook(_audit)
importlib.import_module(module_name)
"""


def test_every_evaluation_module_imports_without_output_or_filesystem_mutation(
    tmp_path: Path,
) -> None:
    modules = _evaluation_modules()
    assert modules, "expected at least one haxlab.evaluation module"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    failures: list[str] = []
    for index, module in enumerate(modules):
        cwd = tmp_path / f"probe-{index:03d}"
        cwd.mkdir()

        completed = subprocess.run(
            [sys.executable, "-c", _IMPORT_PROBE, module],
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        created = sorted(str(path.relative_to(cwd)) for path in cwd.rglob("*"))

        if completed.returncode != 0:
            failures.append(
                f"{module}: import failed rc={completed.returncode}; "
                f"stderr={completed.stderr!r}"
            )
        if completed.stdout:
            failures.append(f"{module}: wrote stdout {completed.stdout!r}")
        if completed.stderr:
            failures.append(f"{module}: wrote stderr {completed.stderr!r}")
        if created:
            failures.append(f"{module}: created cwd entries {created!r}")

    assert not failures, "\n".join(failures)
