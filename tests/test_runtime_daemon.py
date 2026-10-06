import argparse
import math

import pytest

from haxlab.runtime.daemon import _finite_non_negative_seconds


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", 0.0),
        ("0.5", 0.5),
        ("60", 60.0),
        ("1e3", 1000.0),
    ],
)
def test_accepts_finite_non_negative_seconds(raw: str, expected: float) -> None:
    parsed = _finite_non_negative_seconds(raw)

    assert parsed == expected
    assert math.isfinite(parsed)


@pytest.mark.parametrize(
    "raw",
    ["nan", "NaN", "inf", "-inf", "Infinity", "-0.001", "not-a-number"],
)
def test_rejects_invalid_daemon_timing_values(raw: str) -> None:
    with pytest.raises(
        argparse.ArgumentTypeError,
        match="finite non-negative",
    ):
        _finite_non_negative_seconds(raw)
