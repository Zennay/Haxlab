from __future__ import annotations

import sys
from pathlib import Path

import pytest

import haxlab.cli as cli_module
from haxlab.cli import _build_parser


def _parse_confidence(value: str | None, tmp_path: Path):
    export_root = tmp_path / "export"
    export_root.mkdir()
    argv = ["ingest", str(export_root), "--output", str(tmp_path / "output")]
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
def test_ingest_cli_rejects_invalid_match_confidence(
    value: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _parse_confidence(value, tmp_path)

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
    tmp_path: Path,
) -> None:
    args = _parse_confidence(value, tmp_path)

    assert args.minimum_match_confidence == expected


def test_ingest_cli_preserves_default_match_confidence(tmp_path: Path) -> None:
    args = _parse_confidence(None, tmp_path)

    assert args.minimum_match_confidence == 0.65


def test_ingest_cli_accepts_existing_real_export_directory(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()

    args = _build_parser().parse_args(
        ["ingest", str(export_root), "--output", str(tmp_path / "output")]
    )

    assert args.export_root == export_root


@pytest.mark.parametrize("kind", ["missing", "file", "symlink"])
def test_ingest_cli_rejects_invalid_export_root(
    kind: str,
    tmp_path: Path,
) -> None:
    export_root = tmp_path / "export"
    if kind == "file":
        export_root.write_text("not a directory", encoding="utf-8")
    elif kind == "symlink":
        target = tmp_path / "real-export"
        target.mkdir()
        export_root.symlink_to(target, target_is_directory=True)

    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(
            ["ingest", str(export_root), "--output", str(tmp_path / "output")]
        )

    assert exc_info.value.code == 2


def test_ingest_cli_rejects_export_root_through_symlinked_parent(
    tmp_path: Path,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    export_root = real_parent / "export"
    export_root.mkdir()

    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    indirect_export = linked_parent / "export"

    with pytest.raises(SystemExit) as exc_info:
        _build_parser().parse_args(
            ["ingest", str(indirect_export), "--output", str(tmp_path / "output")]
        )

    assert exc_info.value.code == 2


def test_ingest_main_rejects_symlinked_export_parent_before_run_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    export_root = real_parent / "export"
    export_root.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("run_import must not be called")

    monkeypatch.setattr(cli_module, "run_import", fail_if_called)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab",
            "ingest",
            str(linked_parent / "export"),
            "--output",
            str(tmp_path / "output"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()

    assert exc_info.value.code == 2
    assert called is False


def test_ingest_main_rejects_invalid_export_root_before_run_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing-export"
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("run_import must not be called")

    monkeypatch.setattr(cli_module, "run_import", fail_if_called)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab",
            "ingest",
            str(missing),
            "--output",
            str(tmp_path / "output"),
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module.main()

    assert exc_info.value.code == 2
    assert called is False
