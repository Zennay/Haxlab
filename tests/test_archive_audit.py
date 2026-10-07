from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import haxlab.runtime.archive_audit as archive_audit
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
    assert report["read_failures"] == 0
    assert report["objects_with_issues"] == 3
    assert report["issues_truncated"] is False

    by_sha = {item["sha256"]: item["reasons"] for item in report["issues"]}
    assert by_sha[missing_sha] == ["archive_missing"]
    assert by_sha[misplaced_sha] == ["content_address_path_mismatch"]
    assert by_sha[corrupt_sha][0].startswith("sha256_mismatch:")


def test_archive_audit_rejects_regular_file_replacement_before_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    expected_payload = b"expected stable replay"
    digest, path = _write_content_addressed(raw, expected_payload)

    replacement = tmp_path / "replacement.hbr2"
    replacement.write_bytes(b"replacement replay bytes")

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(path),
            size_bytes=len(expected_payload),
        )

        real_open = os.open
        swapped = False

        def swapping_open(target, flags, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and os.fspath(target) == path.name
                and kwargs.get("dir_fd") is not None
            ):
                swapped = True
                replacement.replace(path)
            return real_open(target, flags, *args, **kwargs)

        monkeypatch.setattr(archive_audit.os, "open", swapping_open)
        report = audit_raw_archive(state)

    assert swapped is True
    assert report["ok"] is False
    assert report["existing_files"] == 0
    assert report["read_failures"] == 1
    assert report["hash_mismatches"] == 0
    assert report["issues"][0]["reasons"] == ["archive_identity_changed"]


def test_archive_audit_rejects_in_place_mutation_during_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    expected_payload = b"A" * 4096
    digest, path = _write_content_addressed(raw, expected_payload)

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(path),
            size_bytes=len(expected_payload),
        )

        real_read = os.read
        mutated = False

        def mutating_read(fd, count):
            nonlocal mutated
            chunk = real_read(fd, count)
            if not mutated and chunk:
                mutated = True
                path.write_bytes(b"B" * len(expected_payload))
            return chunk

        monkeypatch.setattr(archive_audit.os, "read", mutating_read)
        report = audit_raw_archive(state)

    assert mutated is True
    assert report["ok"] is False
    assert report["existing_files"] == 0
    assert report["read_failures"] == 1
    assert report["hash_mismatches"] == 0
    assert report["issues"][0]["reasons"] == ["archive_changed_during_read"]


def test_archive_audit_rejects_symlink_replacement_before_open(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    expected_payload = b"expected replay"
    digest, path = _write_content_addressed(raw, expected_payload)

    other = tmp_path / "other.hbr2"
    other.write_bytes(expected_payload)

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(path),
            size_bytes=len(expected_payload),
        )

        real_open = os.open
        swapped = False

        def swapping_open(target, flags, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and os.fspath(target) == path.name
                and kwargs.get("dir_fd") is not None
            ):
                swapped = True
                path.unlink()
                path.symlink_to(other)
            return real_open(target, flags, *args, **kwargs)

        monkeypatch.setattr(archive_audit.os, "open", swapping_open)
        report = audit_raw_archive(state)

    assert swapped is True
    assert report["ok"] is False
    assert report["existing_files"] == 0
    assert report["symlink_entries"] == 1
    assert report["read_failures"] == 0
    assert report["issues"][0]["reasons"] == ["symlink_not_allowed"]


def test_archive_audit_rejects_parent_directory_replacement_during_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    expected_payload = b"stable replay through rebound parent"
    digest, path = _write_content_addressed(raw, expected_payload)
    parent = path.parent
    moved_parent = tmp_path / "original-second-prefix"

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(path),
            size_bytes=len(expected_payload),
        )

        real_open = os.open
        swapped = False

        def swapping_open(target, flags, *args, **kwargs):
            nonlocal swapped
            if (
                not swapped
                and os.fspath(target) == path.name
                and kwargs.get("dir_fd") is not None
            ):
                swapped = True
                parent.rename(moved_parent)
                parent.mkdir()
                (parent / path.name).write_bytes(expected_payload)
            return real_open(target, flags, *args, **kwargs)

        monkeypatch.setattr(archive_audit.os, "open", swapping_open)
        report = audit_raw_archive(state)

    assert swapped is True
    assert report["ok"] is False
    assert report["existing_files"] == 0
    assert report["read_failures"] == 1
    assert report["hash_mismatches"] == 0
    assert report["issues"][0]["reasons"] == [
        "archive_parent_changed_during_read"
    ]
    assert (moved_parent / path.name).read_bytes() == expected_payload
    assert path.read_bytes() == expected_payload


def test_archive_audit_rejects_symlinked_content_address_parent(
    tmp_path: Path,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    payload = b"replay behind parent symlink"
    digest, path = _write_content_addressed(raw, payload)
    parent = path.parent
    external_parent = tmp_path / "external-second-prefix"
    parent.rename(external_parent)
    parent.symlink_to(external_parent, target_is_directory=True)

    with RuntimeState(db) as state:
        state.register_raw(
            sha256=digest,
            archive_path=str(path),
            size_bytes=len(payload),
        )
        report = audit_raw_archive(state)

    assert report["ok"] is False
    assert report["existing_files"] == 0
    assert report["symlink_entries"] == 1
    assert report["read_failures"] == 0
    assert report["issues"][0]["reasons"] == ["symlink_not_allowed"]


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
    assert report["read_failures"] == 0
    assert report["objects_with_issues"] == 1
    assert report["issues"] == []
    assert report["issues_truncated"] is True


def test_archive_audit_rejects_malformed_ledger_evidence_before_file_reads(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = tmp_path / "state.sqlite3"
    raw = tmp_path / "raw"
    good_sha_one = "1" * 64
    good_sha_two = "2" * 64

    with RuntimeState(db) as state:
        state.connection.executemany(
            """
            INSERT INTO raw_replays (sha256, archive_path, size_bytes)
            VALUES (?, ?, ?)
            """,
            [
                ("A" * 64, str(raw / "upper.hbr2"), 1),
                (good_sha_one, "", 1),
                (good_sha_two, str(raw / "bad-size.hbr2"), "not-an-integer"),
                (b"\x00\xff", str(raw / "blob-sha.hbr2"), 1),
            ],
        )
        state.connection.commit()

        def unexpected_read(path: Path) -> tuple[int, str]:
            raise AssertionError(f"malformed ledger row reached filesystem: {path}")

        monkeypatch.setattr(
            archive_audit,
            "_secure_archive_snapshot",
            unexpected_read,
        )
        report = audit_raw_archive(state)

    assert report["ok"] is False
    assert report["checked_records"] == 4
    assert report["existing_files"] == 0
    assert report["read_failures"] == 4
    assert report["objects_with_issues"] == 4
    assert report["issues_truncated"] is False
    assert {tuple(item["reasons"]) for item in report["issues"]} == {
        ("invalid_ledger_evidence:sha256",),
        ("invalid_ledger_evidence:archive_path",),
        ("invalid_ledger_evidence:size_bytes",),
    }
    assert any(item["sha256"] == "<blob:00ff>" for item in report["issues"])
    json.dumps(report)


def test_archive_audit_cli_reports_malformed_size_without_crashing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    db = tmp_path / "state.sqlite3"
    digest = "3" * 64
    archive_path = tmp_path / "raw" / "invalid-size.hbr2"

    with RuntimeState(db) as state:
        state.connection.execute(
            """
            INSERT INTO raw_replays (sha256, archive_path, size_bytes)
            VALUES (?, ?, ?)
            """,
            (digest, str(archive_path), "broken"),
        )
        state.connection.commit()

    monkeypatch.setattr(
        sys,
        "argv",
        ["haxlab-audit-archive", "--state-db", str(db)],
    )
    assert main() == 2
    report = json.loads(capsys.readouterr().out)

    assert report["ok"] is False
    assert report["read_failures"] == 1
    assert report["issues"][0]["reasons"] == [
        "invalid_ledger_evidence:size_bytes"
    ]
