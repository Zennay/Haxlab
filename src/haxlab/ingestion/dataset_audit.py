from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


AUDIT_SCHEMA = "haxlab-m0-dataset-audit-v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DatasetAudit:
    ok: bool
    issues: tuple[str, ...]
    replay_count: int = 0
    report_count: int = 0
    match_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schema"] = AUDIT_SCHEMA
        payload["issues"] = list(self.issues)
        return payload


def _load_json(
    path: Path,
    *,
    label: str,
    expected_type: type,
    issues: list[str],
) -> Any | None:
    if path.is_symlink():
        issues.append(f"{label}:symlink")
        return None
    if not path.is_file():
        issues.append(f"{label}:missing")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        issues.append(f"{label}:invalid_json")
        return None
    if type(payload) is not expected_type:
        issues.append(f"{label}:wrong_type")
        return None
    return payload


def _load_jsonl(path: Path, *, label: str, issues: list[str]) -> list[dict[str, Any]] | None:
    if path.is_symlink():
        issues.append(f"{label}:symlink")
        return None
    if not path.is_file():
        issues.append(f"{label}:missing")
        return None

    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        issues.append(f"{label}:read_failed")
        return None

    for index, line in enumerate(lines, start=1):
        if not line.strip():
            issues.append(f"{label}:blank_line:{index}")
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            issues.append(f"{label}:invalid_json:{index}")
            continue
        if type(row) is not dict:
            issues.append(f"{label}:wrong_type:{index}")
            continue
        rows.append(row)
    return rows


def _is_native_non_negative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _is_finite_probability(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and 0.0 <= float(value) <= 1.0
    )


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return type(value) is list and all(_non_empty_string(item) for item in value)


def audit_dataset(root: Path) -> DatasetAudit:
    """Validate cross-artifact integrity for one M0 import dataset.

    The importer is the producer; this audit is an independent consumer that
    checks that its five published artifacts still describe one coherent dataset.
    It never mutates raw or derived data.
    """

    issues: list[str] = []
    root = Path(root)
    if root.is_symlink():
        return DatasetAudit(False, ("root:symlink",))
    if not root.is_dir():
        return DatasetAudit(False, ("root:not_directory",))

    manifest = _load_json(
        root / "manifest.json",
        label="manifest",
        expected_type=dict,
        issues=issues,
    )
    replays = _load_json(
        root / "replays.json",
        label="replays",
        expected_type=list,
        issues=issues,
    )
    duplicates = _load_json(
        root / "duplicates.json",
        label="duplicates",
        expected_type=dict,
        issues=issues,
    )
    reports = _load_json(
        root / "reports.json",
        label="reports",
        expected_type=list,
        issues=issues,
    )
    matches = _load_jsonl(root / "matches.jsonl", label="matches", issues=issues)

    replay_by_sha: dict[str, dict[str, Any]] = {}
    replay_by_source: dict[str, dict[str, Any]] = {}
    valid_replay_sources: set[str] = set()

    if replays is not None:
        for index, row in enumerate(replays):
            prefix = f"replays:{index}"
            if type(row) is not dict:
                issues.append(f"{prefix}:wrong_type")
                continue

            sha = row.get("sha256")
            source_path = row.get("source_path")
            file_name = row.get("file_name")
            size_bytes = row.get("size_bytes")
            validation = row.get("basic_validation")

            if not isinstance(sha, str) or _SHA256_RE.fullmatch(sha) is None:
                issues.append(f"{prefix}:invalid_sha256")
            elif sha in replay_by_sha:
                issues.append(f"{prefix}:duplicate_sha256")
            else:
                replay_by_sha[sha] = row

            if not _non_empty_string(source_path):
                issues.append(f"{prefix}:invalid_source_path")
            elif source_path in replay_by_source:
                issues.append(f"{prefix}:duplicate_source_path")
            else:
                replay_by_source[source_path] = row

            if not _non_empty_string(file_name):
                issues.append(f"{prefix}:invalid_file_name")
            if not _is_native_non_negative_int(size_bytes):
                issues.append(f"{prefix}:invalid_size_bytes")

            if type(validation) is not dict:
                issues.append(f"{prefix}:invalid_basic_validation")
                continue
            valid = validation.get("valid")
            reasons = validation.get("reasons")
            if type(valid) is not bool:
                issues.append(f"{prefix}:invalid_validation_flag")
            if not _string_list(reasons):
                issues.append(f"{prefix}:invalid_validation_reasons")
            if valid is True and _non_empty_string(source_path):
                valid_replay_sources.add(source_path)

    report_ids: set[str] = set()
    if reports is not None:
        for index, row in enumerate(reports):
            prefix = f"reports:{index}"
            if type(row) is not dict:
                issues.append(f"{prefix}:wrong_type")
                continue
            message_id = row.get("message_id")
            if not _non_empty_string(message_id):
                issues.append(f"{prefix}:invalid_message_id")
            elif message_id in report_ids:
                issues.append(f"{prefix}:duplicate_message_id")
            else:
                report_ids.add(message_id)

    duplicate_count = 0
    if duplicates is not None:
        seen_duplicate_paths: set[str] = set()
        for sha, paths in duplicates.items():
            if not isinstance(sha, str) or _SHA256_RE.fullmatch(sha) is None:
                issues.append("duplicates:invalid_sha256")
                continue
            if sha not in replay_by_sha:
                issues.append(f"duplicates:{sha}:unknown_replay")
            if not _string_list(paths):
                issues.append(f"duplicates:{sha}:invalid_paths")
                continue
            duplicate_count += len(paths)
            for path in paths:
                if path in seen_duplicate_paths:
                    issues.append(f"duplicates:{sha}:duplicate_path")
                seen_duplicate_paths.add(path)
                canonical = replay_by_sha.get(sha, {}).get("source_path")
                if canonical == path:
                    issues.append(f"duplicates:{sha}:canonical_path_reused")

    match_ids: set[str] = set()
    matched_replay_shas: set[str] = set()
    matched_replay_sources: set[str] = set()
    matched_report_ids: set[str] = set()

    if matches is not None:
        for index, row in enumerate(matches):
            prefix = f"matches:{index}"
            if type(row.get("schema_version")) is not int or row.get("schema_version") != 1:
                issues.append(f"{prefix}:invalid_schema_version")

            match_id = row.get("match_id")
            if not _non_empty_string(match_id):
                issues.append(f"{prefix}:invalid_match_id")
            elif match_id in match_ids:
                issues.append(f"{prefix}:duplicate_match_id")
            else:
                match_ids.add(match_id)

            sha = row.get("replay_sha256")
            source_path = row.get("replay_source_path")
            if not isinstance(sha, str) or _SHA256_RE.fullmatch(sha) is None:
                issues.append(f"{prefix}:invalid_replay_sha256")
            elif sha not in replay_by_sha:
                issues.append(f"{prefix}:unknown_replay_sha256")
            elif sha in matched_replay_shas:
                issues.append(f"{prefix}:duplicate_replay_match")
            else:
                matched_replay_shas.add(sha)
                canonical_source = replay_by_sha[sha].get("source_path")
                if source_path != canonical_source:
                    issues.append(f"{prefix}:replay_source_mismatch")
                elif _non_empty_string(source_path):
                    matched_replay_sources.add(source_path)

            source_message_id = row.get("source_message_id")
            report = row.get("report")
            if source_message_id is None:
                if report is not None:
                    issues.append(f"{prefix}:unexpected_report")
            elif not _non_empty_string(source_message_id):
                issues.append(f"{prefix}:invalid_source_message_id")
            elif source_message_id not in report_ids:
                issues.append(f"{prefix}:unknown_source_message_id")
            else:
                if source_message_id in matched_report_ids:
                    issues.append(f"{prefix}:duplicate_report_match")
                matched_report_ids.add(source_message_id)
                if type(report) is not dict:
                    issues.append(f"{prefix}:missing_report")
                elif report.get("message_id") != source_message_id:
                    issues.append(f"{prefix}:report_message_id_mismatch")

            if not _is_finite_probability(row.get("match_confidence")):
                issues.append(f"{prefix}:invalid_match_confidence")
            if not _string_list(row.get("match_reasons")):
                issues.append(f"{prefix}:invalid_match_reasons")

    if manifest is not None:
        if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
            issues.append("manifest:invalid_schema_version")

        expected_counts = {
            "replay_count": (len(replay_by_sha) + duplicate_count)
            if replays is not None and duplicates is not None
            else None,
            "unique_replay_count": len(replay_by_sha) if replays is not None else None,
            "duplicate_replay_count": duplicate_count if duplicates is not None else None,
            "report_count": len(report_ids) if reports is not None else None,
            "match_count": len(matches) if matches is not None else None,
        }
        for key, expected in expected_counts.items():
            actual = manifest.get(key)
            if not _is_native_non_negative_int(actual):
                issues.append(f"manifest:{key}:invalid")
            elif expected is not None and actual != expected:
                issues.append(f"manifest:{key}:mismatch")

        unmatched_replays = manifest.get("unmatched_replays")
        if not _string_list(unmatched_replays):
            issues.append("manifest:unmatched_replays:invalid")
        elif len(unmatched_replays) != len(set(unmatched_replays)):
            issues.append("manifest:unmatched_replays:duplicates")
        elif replays is not None and set(unmatched_replays) != (
            valid_replay_sources - matched_replay_sources
        ):
            issues.append("manifest:unmatched_replays:mismatch")

        unmatched_reports = manifest.get("unmatched_reports")
        if not _string_list(unmatched_reports):
            issues.append("manifest:unmatched_reports:invalid")
        elif len(unmatched_reports) != len(set(unmatched_reports)):
            issues.append("manifest:unmatched_reports:duplicates")
        elif reports is not None and set(unmatched_reports) != (
            report_ids - matched_report_ids
        ):
            issues.append("manifest:unmatched_reports:mismatch")

    return DatasetAudit(
        ok=not issues,
        issues=tuple(issues),
        replay_count=len(replay_by_sha),
        report_count=len(report_ids),
        match_count=len(matches or []),
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m haxlab.ingestion.dataset_audit")
    parser.add_argument("root", type=Path)
    args = parser.parse_args()

    audit = audit_dataset(args.root)
    print(json.dumps(audit.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
