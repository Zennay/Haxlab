import argparse
import sys
from pathlib import Path

import pytest

from haxlab.runtime import daemon as daemon_module
from haxlab.runtime.daemon import _minimum_file_age_arg, _poll_interval_arg


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", 1.0),
        ("1.0001", 1.0001),
        ("1.25", 1.25),
        ("60", 60.0),
        ("1e3", 1000.0),
    ],
)
def test_poll_interval_accepts_canonical_finite_values(
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
        "0.999",
        "-1",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "1e309",
        "not-a-number",
    ],
)
def test_poll_interval_rejects_unsafe_values(raw: str) -> None:
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="interval must be a finite number greater than or equal to 1",
    ):
        _poll_interval_arg(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0.0),
        ("1e-3", 0.001),
        ("0.25", 0.25),
        ("30", 30.0),
        ("1e3", 1000.0),
    ],
)
def test_minimum_file_age_accepts_canonical_non_negative_values(
    raw: str,
    expected: float,
) -> None:
    assert _minimum_file_age_arg(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " 0",
        "0 ",
        "-0.001",
        "nan",
        "NaN",
        "inf",
        "-inf",
        "1e309",
        "not-a-number",
    ],
)
def test_minimum_file_age_rejects_unsafe_values(raw: str) -> None:
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="minimum file age must be a finite number greater than or equal to 0",
    ):
        _minimum_file_age_arg(raw)


@pytest.mark.parametrize(
    ("flag", "raw_value"),
    [
        ("--interval", "0"),
        ("--interval", "nan"),
        ("--interval", " 60"),
        ("--minimum-file-age", "-1"),
        ("--minimum-file-age", "inf"),
        ("--minimum-file-age", "30 "),
    ],
)
def test_main_rejects_invalid_timing_before_filesystem_or_state_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    flag: str,
    raw_value: str,
) -> None:
    incoming = tmp_path / "incoming"
    raw = tmp_path / "raw"
    state_db = tmp_path / "state" / "haxlab.sqlite3"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-daemon",
            "--incoming",
            str(incoming),
            "--raw",
            str(raw),
            "--state-db",
            str(state_db),
            flag,
            raw_value,
            "--once",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        daemon_module.main()

    assert exc_info.value.code == 2
    assert not incoming.exists()
    assert not raw.exists()
    assert not state_db.exists()


def test_parser_keeps_production_defaults() -> None:
    args = daemon_module._build_parser().parse_args(
        [
            "--incoming",
            "/tmp/incoming",
            "--raw",
            "/tmp/raw",
            "--state-db",
            "/tmp/state.sqlite3",
            "--once",
        ]
    )

    assert args.interval == 60.0
    assert args.minimum_file_age == 30.0
