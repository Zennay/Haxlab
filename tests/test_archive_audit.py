from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from haxlab.runtime.archive import archive_path_for
from haxlab.runtime.archive_audit import AUDIT_SCHEMA, audit_raw_archive, main
from haxlab.runtime.state import RuntimeState


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_content_addressed(root: Path, payload: bytes) -> tuple[str, Path]:
    digest = _sha(payload)
    path = archive_path_for(root, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return digest, path


def test_archive_audit_reports_missing_hash_and_path_failures(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"

    valid_payload = b"valid"
    valid_sha, valid_path = _write_content_addressed(raw, valid_payload)

    missing_payload = b"missing"
    missing_sha = _sha(missing_payload)
    missing_path = archive_path_for(raw, missing_sha)

    corrupt_expected = b"abc"
    corrupt_actual = b"abd"
    corrupt_sha = _sha(corrupt_expected)
    corrupt_path = archive_path_for(raw, corrupt_sha)
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_bytes(corrupt_actual)

    misplaced_payload = b"misplaced"
    misplaced_sha = _sha(misplaced_payload)
    misplaced_path = raw / "misplaced.hbr2"
    misplaced_path.write_bytes(misplaced_payload)

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=valid_sha,
            archive_path=str(valid_path),
            size_bytes=len(valid_payload),
        )
        state.register_raw(
            sha256=missing_sha,
            archive_path=str(missing_path),
            size_bytes=len(missing_payload),
        )
        state.register_raw(
            sha256=corrupt_sha,
            archive_path=str(corrupt_path),
            size_bytes=len(corrupt_expected),
        )
        state.register_raw(
            sha256=misplaced_sha,
            archive_path=str(misplaced_path),
            size_bytes=len(misplaced_payload),
        )

        report = audit_raw_archive(state)

    assert report["schema"] == AUDIT_SCHEMA
    assert report["ok"] is False
    assert report["checked_records"] == 4
    assert report["existing_files"] == 3
    assert report["missing_files"] == 1
    assert report["size_mismatches"] == 0
    assert report["hash_mismatches"] == 1
    assert report["path_mismatches"] == 1
    assert report["symlink_entries"] == 0
    assert report["objects_with_issues"] == 3
    assert report["issues_truncated"] is False

    by_sha = {item["sha256"]: item["reasons"] for item in report["issues"]}
    assert by_sha[missing_sha] == ["archive_missing"]
    assert by_sha[misplaced_sha] == ["content_address_path_mismatch"]
    assert by_sha[corrupt_sha][0].startswith("sha256_mismatch:")


def test_archive_audit_cli_fails_closed_and_can_truncate_details(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    payload = b"missing"
    digest = _sha(payload)
    missing_path = archive_path_for(tmp_path / "raw", digest)

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(missing_path),
            size_bytes=len(payload),
        )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "haxlab-audit-archive",
            "--state-db",
            str(db),
            "--max-issues",
            "0",
        ],
    )
    assert main() == 2
    report = json.loads(capsys.readouterr().out)

    assert report["ok"] is False
    assert report["missing_files"] == 1
    assert report["objects_with_issues"] == 1
    assert report["issues"] == []
    assert report["issues_truncated"] is True
