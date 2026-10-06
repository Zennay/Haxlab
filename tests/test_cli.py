import argparse
import math
from pathlib import Path

import pytest

from haxlab.cli import _bounded_confidence, _build_parser, _existing_directory


def test_existing_directory_accepts_real_directory(tmp_path: Path) -> None:
    assert _existing_directory(str(tmp_path)) == tmp_path


def test_existing_directory_rejects_missing_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(argparse.ArgumentTypeError, match="directory does not exist"):
        _existing_directory(str(missing))


def test_existing_directory_rejects_regular_file(tmp_path: Path) -> None:
    source = tmp_path / "export.json"
    source.write_text("{}", encoding="utf-8")

    with pytest.raises(argparse.ArgumentTypeError, match="not a directory"):
        _existing_directory(str(source))


@pytest.mark.parametrize("raw", ["nan", "inf", "-inf", "-0.01", "1.01", "bad"])
def test_bounded_confidence_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError, match=r"finite number in \[0, 1\]"):
        _bounded_confidence(raw)


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("0.65", 0.65), ("1", 1.0)])
def test_bounded_confidence_accepts_closed_interval(raw: str, expected: float) -> None:
    parsed = _bounded_confidence(raw)

    assert parsed == expected
    assert math.isfinite(parsed)


def test_ingest_parser_rejects_missing_export_root(tmp_path: Path) -> None:
    parser = _build_parser()

    with pytest.raises(SystemExit) as exc:
        parser.parse_args(
            [
                "ingest",
                str(tmp_path / "missing"),
                "--output",
                str(tmp_path / "derived"),
            ]
        )

    assert exc.value.code == 2
