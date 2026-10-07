from __future__ import annotations

import pytest

from haxlab.runtime.worker import _build_parser


def _parse_batch_size(value: str | None):
    argv = ["--state-db", "/tmp/haxlab-worker-state.sqlite3", "--once"]
    if value is not None:
        argv.extend(["--batch-size", value])
    return _build_parser().parse_args(argv)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0",
        "-1",
        "-50",
        "1.5",
        "nan",
        "inf",
        " 1",
        "1 ",
        "workers",
    ],
)
def test_worker_cli_rejects_invalid_batch_size(value: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_batch_size(value)

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", 1),
        ("2", 2),
        ("50", 50),
        ("1000", 1000),
    ],
)
def test_worker_cli_accepts_positive_batch_size(
    value: str,
    expected: int,
) -> None:
    args = _parse_batch_size(value)

    assert args.batch_size == expected


def test_worker_cli_preserves_default_batch_size() -> None:
    args = _parse_batch_size(None)

    assert args.batch_size == 50
