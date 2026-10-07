from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from haxlab.ingestion import source_bundle_audit as audit
from haxlab.ingestion import source_bundle_receipt as receipt


def _write_sources(root: Path) -> None:
    (root / "nested").mkdir(parents=True, exist_ok=True)
    (root / "discord").mkdir(parents=True, exist_ok=True)
    (root / "nested" / "match.HBR2").write_bytes(b"replay-bytes")
    (root / "discord" / "export.json").write_text(
        '{"messages": []}\n',
        encoding="utf-8",
    )
    (root / "discord" / "ignored.JSON").write_text("{}", encoding="utf-8")
    (root / "notes.txt").write_text("ignored", encoding="utf-8")


def _publish_receipt(root: Path, target: Path) -> dict[str, object]:
    value = receipt.create_source_bundle_receipt(root)
    target.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return value


def _resign(value: dict[str, object]) -> dict[str, object]:
    unsigned = dict(value)
    unsigned.pop("receipt_sha256", None)
    value["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return value


def _rewrite(path: Path, value: dict[str, object]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def test_audit_accepts_exact_published_receipt(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    published = _publish_receipt(root, receipt_path)

    result = audit.audit_source_bundle(root, receipt_path)

    assert result == {
        "schema": audit.AUDIT_SCHEMA,
        "clean": True,
        "receipt_schema": audit.RECEIPT_SCHEMA,
        "receipt_sha256": published["receipt_sha256"],
        "file_count": 2,
        "hbr2_count": 1,
        "discord_json_count": 1,
        "total_size_bytes": len(b"replay-bytes") + len(b'{"messages": []}\n'),
    }


def test_audit_rejects_source_byte_drift(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    (root / "nested" / "match.HBR2").write_bytes(b"changed-replay")

    with pytest.raises(
        audit.SourceBundleAuditError,
        match="source_(size|sha256)_mismatch:nested/match.HBR2",
    ):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_added_importer_source(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    (root / "late.hbr2").write_bytes(b"late")

    with pytest.raises(audit.SourceBundleAuditError, match="source_inventory_mismatch"):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_ignores_non_importer_source_changes(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    (root / "notes.txt").write_text("changed", encoding="utf-8")
    (root / "discord" / "ignored.JSON").write_text('{"changed": true}', encoding="utf-8")

    assert audit.audit_source_bundle(root, receipt_path)["clean"] is True


def test_audit_rejects_source_symlink_after_publication(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    source = root / "nested" / "match.HBR2"
    source.unlink()
    outside = tmp_path / "outside.hbr2"
    outside.write_bytes(b"replay-bytes")
    source.symlink_to(outside)

    with pytest.raises(
        audit.SourceBundleAuditError,
        match="source_file:nested/match.HBR2_symlink",
    ):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_symlinked_receipt(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    actual = tmp_path / "actual.txt"
    _publish_receipt(root, actual)
    linked = tmp_path / "linked.txt"
    linked.symlink_to(actual)

    with pytest.raises(audit.SourceBundleAuditError, match="receipt_file_symlink"):
        audit.audit_source_bundle(root, linked)


def test_audit_rejects_receipt_digest_drift(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    value["file_count"] = 99
    _rewrite(receipt_path, value)

    with pytest.raises(audit.SourceBundleAuditError, match="receipt_file_count_mismatch"):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_resigned_non_native_integer(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    value["files"][0]["size_bytes"] = True  # type: ignore[index]
    _resign(value)
    _rewrite(receipt_path, value)

    with pytest.raises(audit.SourceBundleAuditError, match="receipt_invalid_size_bytes"):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_resigned_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    value["files"][0]["path"] = "../outside.json"  # type: ignore[index]
    _resign(value)
    _rewrite(receipt_path, value)

    with pytest.raises(audit.SourceBundleAuditError, match="receipt_invalid_path"):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_unknown_receipt_fields_even_when_resigned(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    value["unexpected"] = "future ambiguity"
    _resign(value)
    _rewrite(receipt_path, value)

    with pytest.raises(
        audit.SourceBundleAuditError,
        match="receipt_top_level_fields_mismatch",
    ):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = raw.replace(
        f'"schema":"{receipt.SCHEMA}"',
        f'"schema":"duplicate","schema":"{receipt.SCHEMA}"',
        1,
    )
    receipt_path.write_text(raw, encoding="utf-8")

    with pytest.raises(audit.SourceBundleAuditError, match="receipt_duplicate_key:schema"):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_non_standard_json_constants(tmp_path: Path) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    value = _publish_receipt(root, receipt_path)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = raw.replace('"total_size_bytes":', '"total_size_bytes":NaN,"shadow":', 1)
    receipt_path.write_text(raw, encoding="utf-8")

    with pytest.raises(
        audit.SourceBundleAuditError,
        match="receipt_non_finite_number:NaN",
    ):
        audit.audit_source_bundle(root, receipt_path)


def test_audit_rejects_inventory_drift_during_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    original = audit._inventory_paths
    calls = 0

    def unstable(path: Path):
        nonlocal calls
        calls += 1
        result = original(path)
        if calls == 1:
            (root / "late.json").write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(audit, "_inventory_paths", unstable)

    with pytest.raises(
        audit.SourceBundleAuditError,
        match="source_inventory_changed_during_audit",
    ):
        audit.audit_source_bundle(root, receipt_path)


def test_cli_is_machine_readable_on_success_and_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / "sources"
    root.mkdir()
    _write_sources(root)
    receipt_path = tmp_path / "receipt.txt"
    _publish_receipt(root, receipt_path)

    assert audit.main([str(root), str(receipt_path)]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["schema"] == audit.AUDIT_SCHEMA
    assert success["clean"] is True

    (root / "late.json").write_text("{}", encoding="utf-8")
    assert audit.main([str(root), str(receipt_path)]) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["schema"] == audit.AUDIT_SCHEMA
    assert failure["clean"] is False
    assert failure["error"] == "source_inventory_mismatch"
