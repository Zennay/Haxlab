from __future__ import annotations

import pytest

from haxlab.cli import _build_parser


def _parse_confidence(value: str | None):
    argv = ["ingest", "/tmp/export", "--output", "/tmp/output"]
    if value is not None:
        argv.extend(["--minimum-match-confidence", value])
    return _build_parser().parse_args(argv)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "nan",
        "NaN",
        "inf",
        "+inf",
        "-inf",
        "-0.0001",
        "1.0001",
        " 0.5",
        "0.5 ",
    ],
)
def test_ingest_cli_rejects_invalid_match_confidence(value: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_confidence(value)

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", 0.0),
        ("0.25", 0.25),
        ("6.5e-1", 0.65),
        ("1", 1.0),
    ],
)
def test_ingest_cli_accepts_finite_match_confidence_in_unit_interval(
    value: str,
    expected: float,
) -> None:
    args = _parse_confidence(value)

    assert args.minimum_match_confidence == expected


def test_ingest_cli_preserves_default_match_confidence() -> None:
    args = _parse_confidence(None)

    assert args.minimum_match_confidence == 0.65
