from __future__ import annotations

from collections import Counter
from pathlib import Path
import re
import shlex

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
PIPELINE_UNITS = (
    "haxlab-ingest.service",
    "haxlab-worker.service",
    "haxlab-analyzer.service",
)


def _execstart_argv(unit_text: str) -> list[str]:
    """Extract the single logical ExecStart command from an explicit unit."""
    logical_text = re.sub(r"\\\r?\n[ \t]*", " ", unit_text)
    commands = re.findall(r"^ExecStart=(.*)$", logical_text, flags=re.MULTILINE)
    if len(commands) != 1:
        raise ValueError(f"expected_single_execstart:found={len(commands)}")
    return shlex.split(commands[0])


def _repeated_long_options(argv: list[str]) -> list[str]:
    options: list[str] = []
    for token in argv:
        if not token.startswith("--"):
            continue
        name = token.partition("=")[0]
        if re.fullmatch(r"--[a-z][a-z0-9-]*", name):
            options.append(name)
    counts = Counter(options)
    return sorted(name for name, count in counts.items() if count > 1)


def test_deployed_data_pipeline_service_flags_have_no_overrides() -> None:
    for unit in PIPELINE_UNITS:
        argv = _execstart_argv((DEPLOY / unit).read_text(encoding="utf-8"))
        assert _repeated_long_options(argv) == [], (
            f"{unit}: duplicate long options can override checked values"
        )


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("python -m worker --interval 5 --interval inf", ["--interval"]),
        ("python -m worker --interval=5 --interval=inf", ["--interval"]),
        ("python -m worker --interval 5 --interval=inf", ["--interval"]),
        (
            "python -m analyzer --workers 6 --interval 5 --interval 2 --workers=7",
            ["--interval", "--workers"],
        ),
    ],
)
def test_duplicate_option_spellings_are_detected(
    command: str, expected: list[str]
) -> None:
    argv = _execstart_argv("[Service]\nExecStart=" + command + "\n")
    assert _repeated_long_options(argv) == expected


def test_distinct_option_values_and_line_continuations_remain_allowed() -> None:
    unit = (
        "[Service]\nExecStart=/opt/haxlab/.venv/bin/python -m haxlab.runtime.worker "
        "\\\n  --interval 5 \\\n  --batch-size=50\n"
    )
    assert _repeated_long_options(_execstart_argv(unit)) == []


@pytest.mark.parametrize(
    "unit",
    (
        "[Service]\n",
        "[Service]\nExecStart=python -m worker\nExecStart=python -m analyzer\n",
    ),
)
def test_missing_or_ambiguous_execstart_fails_closed(unit: str) -> None:
    with pytest.raises(ValueError, match="expected_single_execstart"):
        _execstart_argv(unit)
