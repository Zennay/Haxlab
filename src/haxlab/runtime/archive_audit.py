from __future__ import annotations

import argparse
import json
from pathlib import Path

from haxlab.hashing import sha256_file
from haxlab.runtime.state import RuntimeState


AUDIT_SCHEMA = "haxlab-raw-archive-audit-v1"


def _content_address_path_matches(path: Path, sha256: str) -> bool:
    return (
        path.name == f"{sha256}.hbr2"
        and path.parent.name == sha256[2:4]
        and path.parent.parent.name == sha256[:2]
    )


def audit_raw_archive(
    state: RuntimeState,
    *,
    max_issues: int = 100,
) -> dict[str, object]:
    """Verify every raw_replays ledger row against immutable on-disk evidence."""

    rows = state.connection.execute(
        """
        SELECT sha256, archive_path, size_bytes
        FROM raw_replays
        ORDER BY sha256
        """
    ).fetchall()

    checked_records = 0
    existing_files = 0
    missing_files = 0
    size_mismatches = 0
    hash_mismatches = 0
    path_mismatches = 0
    symlink_entries = 0
    objects_with_issues = 0
    issues: list[dict[str, object]] = []

    for row in rows:
        checked_records += 1
        sha256 = str(row["sha256"])
        archive_path = str(row["archive_path"])
        expected_size = int(row["size_bytes"])
        path = Path(archive_path)
        reasons: list[str] = []

        if not _content_address_path_matches(path, sha256):
            path_mismatches += 1
            reasons.append("content_address_path_mismatch")

        if path.is_symlink():
            symlink_entries += 1
            reasons.append("symlink_not_allowed")
        elif not path.is_file():
            missing_files += 1
            reasons.append("archive_missing")
        else:
            existing_files += 1
            actual_size = path.stat().st_size
            if actual_size != expected_size:
                size_mismatches += 1
                reasons.append(
                    f"size_mismatch:expected={expected_size}:actual={actual_size}"
                )

            actual_sha256 = sha256_file(path)
            if actual_sha256 != sha256:
                hash_mismatches += 1
                reasons.append(
                    f"sha256_mismatch:expected={sha256}:actual={actual_sha256}"
                )

        if reasons:
            objects_with_issues += 1
            if len(issues) < max(0, max_issues):
                issues.append(
                    {
                        "sha256": sha256,
                        "archive_path": archive_path,
                        "reasons": reasons,
                    }
                )

    return {
        "schema": AUDIT_SCHEMA,
        "ok": objects_with_issues == 0,
        "checked_records": checked_records,
        "existing_files": existing_files,
        "missing_files": missing_files,
        "size_mismatches": size_mismatches,
        "hash_mismatches": hash_mismatches,
        "path_mismatches": path_mismatches,
        "symlink_entries": symlink_entries,
        "objects_with_issues": objects_with_issues,
        "issues": issues,
        "issues_truncated": objects_with_issues > len(issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-audit-archive")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument("--max-issues", type=int, default=100)
    args = parser.parse_args()

    with RuntimeState(args.state_db) as state:
        report = audit_raw_archive(state, max_issues=args.max_issues)

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["ok"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
