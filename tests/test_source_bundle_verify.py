from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.ingestion import source_bundle_verify as verify
from haxlab.ingestion.source_bundle_receipt import create_source_bundle_receipt


def _source_tree(root: Path) -> None:
    (root / "discord").mkdir(parents=True)
    (root / "match.hbr2").write_bytes(b"replay-a")
    (root / "discord" / "export.json").write_text(
        '{"messages": []}\n',
        encoding="utf-8",
    )


def _canonical_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _resign(payload: dict) -> None:
    unsigned = dict(payload)
    unsigned.pop("receipt_sha256", None)
    payload["receipt_sha256"] = hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def _write_receipt(export_root: Path, receipt_path: Path) -> dict:
    payload = create_source_bundle_receipt(export_root)
    receipt_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def test_verify_accepts_exact_persisted_receipt(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    expected = _write_receipt(export_root, receipt_path)

    result = verify.verify_source_bundle(export_root, receipt_path)

    assert result == {
        "schema": verify.VERIFY_SCHEMA,
        "clean": True,
        "expected_receipt_sha256": expected["receipt_sha256"],
        "current_receipt_sha256": expected["receipt_sha256"],
        "missing_sources": [],
        "unexpected_sources": [],
        "changed_sources": [],
    }


def test_verify_reports_changed_source_bytes(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    expected = _write_receipt(export_root, receipt_path)

    (export_root / "match.hbr2").write_bytes(b"replay-b")
    result = verify.verify_source_bundle(export_root, receipt_path)

    assert result["clean"] is False
    assert result["expected_receipt_sha256"] == expected["receipt_sha256"]
    assert result["current_receipt_sha256"] != expected["receipt_sha256"]
    assert result["changed_sources"] == ["match.hbr2"]
    assert result["missing_sources"] == []
    assert result["unexpected_sources"] == []


def test_verify_reports_added_and_missing_sources(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(export_root, receipt_path)

    (export_root / "match.hbr2").unlink()
    (export_root / "new.hbr2").write_bytes(b"new")
    result = verify.verify_source_bundle(export_root, receipt_path)

    assert result["clean"] is False
    assert result["missing_sources"] == ["match.hbr2"]
    assert result["unexpected_sources"] == ["new.hbr2"]
    assert result["changed_sources"] == []


def test_verify_rejects_unknown_receipt_fields(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = _write_receipt(export_root, receipt_path)
    payload["surprise"] = True
    _resign(payload)
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match="receipt_fields_mismatch",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    text = json.dumps(payload, separators=(",", ":"))
    text = text.replace(
        '"file_count":2',
        '"file_count":2,"file_count":2',
        1,
    )
    receipt_path.write_text(text, encoding="utf-8")

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match="duplicate_json_key:file_count",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_verify_rejects_nonstandard_numeric_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    text = json.dumps(payload, separators=(",", ":")).replace(
        '"total_size_bytes":29',
        f'"total_size_bytes":{constant}',
        1,
    )
    receipt_path.write_text(text, encoding="utf-8")

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match=f"invalid_json_constant:{constant}",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_coercible_count(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    payload["file_count"] = True
    _resign(payload)
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(verify.SourceBundleVerifyError, match="invalid_file_count"):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_unsafe_relative_path(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    payload["files"][0]["path"] = "../escape.json"
    _resign(payload)
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(verify.SourceBundleVerifyError, match="invalid_file_path"):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_noncanonical_file_order(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    payload["files"].reverse()
    _resign(payload)
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match="files_not_canonically_ordered",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_bad_self_digest(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    payload = create_source_bundle_receipt(export_root)
    payload["receipt_sha256"] = "0" * 64
    receipt_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match="receipt_self_digest_mismatch",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


def test_verify_rejects_symlinked_receipt(tmp_path: Path) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    target = tmp_path / "actual.json"
    _write_receipt(export_root, target)
    link = tmp_path / "receipt.json"
    link.symlink_to(target)

    with pytest.raises(verify.SourceBundleVerifyError, match="receipt_symlink"):
        verify.verify_source_bundle(export_root, link)


def test_verify_rejects_receipt_mutation_during_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(export_root, receipt_path)

    original = verify.create_source_bundle_receipt

    def mutate_after_hash(root: Path) -> dict:
        current = original(root)
        receipt_path.write_bytes(receipt_path.read_bytes() + b" ")
        return current

    monkeypatch.setattr(verify, "create_source_bundle_receipt", mutate_after_hash)

    with pytest.raises(
        verify.SourceBundleVerifyError,
        match="receipt_changed_during_verification",
    ):
        verify.verify_source_bundle(export_root, receipt_path)


def test_cli_exit_codes_are_machine_readable(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    export_root = tmp_path / "export"
    export_root.mkdir()
    _source_tree(export_root)
    receipt_path = tmp_path / "receipt.json"
    _write_receipt(export_root, receipt_path)

    assert verify.main([str(export_root), str(receipt_path)]) == 0
    clean = json.loads(capsys.readouterr().out)
    assert clean["clean"] is True

    (export_root / "match.hbr2").write_bytes(b"changed")
    assert verify.main([str(export_root), str(receipt_path)]) == 1
    drift = json.loads(capsys.readouterr().out)
    assert drift["clean"] is False
    assert drift["changed_sources"] == ["match.hbr2"]

    receipt_path.write_text("not json", encoding="utf-8")
    assert verify.main([str(export_root), str(receipt_path)]) == 2
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["clean"] is False
    assert invalid["error"].startswith("invalid_receipt_json:")
