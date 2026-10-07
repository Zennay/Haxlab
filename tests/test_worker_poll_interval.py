import argparse
import sys

import pytest

import haxlab.runtime.worker as worker_module
from haxlab.runtime.worker import _poll_interval_arg


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1.0),
        ("1.25", 1.25),
        ("5", 5.0),
        ("1e3", 1000.0),
    ],
)
def test_poll_interval_accepts_finite_values_at_least_one_second(
    raw: str,
    expected: float,
) -> None:
    assert _poll_interval_arg(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " 1",
        "1 ",
        "0",
        "-1",
        "0.999999",
        "nan",
        "NaN",
        "inf",
        "+inf",
        "-inf",
        "Infinity",
        "1e309",
        "not-a-number",
    ],
)
def test_poll_interval_rejects_noncanonical_or_unsafe_values(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _poll_interval_arg(raw)


@pytest.mark.parametrize("value", [True, 1, 1.0, None, b"1"])
def test_poll_interval_rejects_non_cli_native_values(value: object) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        _poll_interval_arg(value)  # type: ignore[arg-type]


def test_invalid_poll_interval_fails_before_runtime_state_opens(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    opened: list[object] = []

    def forbidden_runtime_state(*args: object, **kwargs: object) -> object:
        opened.append((args, kwargs))
        raise AssertionError("RuntimeState must not open for an invalid poll interval")

    monkeypatch.setattr(worker_module, "RuntimeState", forbidden_runtime_state)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-worker",
            "--state-db",
            str(tmp_path / "state.sqlite3"),
            "--interval",
            "nan",
            "--once",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        worker_module.main()

    assert exc_info.value.code == 2
    assert opened == []
