#!/usr/bin/env python3
"""Bounded, read-only failure-stage triage of one M0 import manifest.

Not a re-import, source provenance verifier, publication comparator or gate.
No source filenames or raw producer errors are included in the report.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

SCHEMA = "haxlab-m0-import-failure-triage-v1"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
KNOWN_STAGES = frozenset(("discord_json", "discord_messages", "hbr2_validation"))
COUNTS = ("replay_count", "unique_replay_count", "duplicate_replay_count",
          "report_count", "match_count")


class TriageError(ValueError):
    """An input that cannot safely support a diagnostic count report."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TriageError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise TriageError("nonfinite_json_constant")


def _identity(meta: os.stat_result) -> tuple[int, int, int, int, int]:
    return (meta.st_dev, meta.st_ino, meta.st_size, meta.st_mtime_ns, meta.st_ctime_ns)


def _read_manifest(root: Path) -> bytes:
    if not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY")):
        raise TriageError("platform_lacks_nofollow")
    try:
        original = root.lstat()
    except OSError:
        raise TriageError("unsafe_or_missing_root") from None
    if not stat.S_ISDIR(original.st_mode) or stat.S_ISLNK(original.st_mode):
        raise TriageError("unsafe_or_missing_root")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(root, flags)
    except OSError:
        raise TriageError("unsafe_or_missing_root") from None
    try:
        opened_root = os.fstat(root_fd)
        if (original.st_dev, original.st_ino) != (opened_root.st_dev, opened_root.st_ino):
            raise TriageError("root_identity_changed")
        try:
            initial = os.stat("manifest.json", dir_fd=root_fd, follow_symlinks=False)
        except OSError:
            raise TriageError("unsafe_or_missing_manifest") from None
        if stat.S_ISLNK(initial.st_mode):
            raise TriageError("unsafe_or_missing_manifest")
        if not stat.S_ISREG(initial.st_mode):
            raise TriageError("non_regular_manifest")
        try:
            fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW |
                         getattr(os, "O_CLOEXEC", 0), dir_fd=root_fd)
        except OSError:
            raise TriageError("unsafe_or_missing_manifest") from None
        with os.fdopen(fd, "rb") as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or _identity(initial) != _identity(before):
                raise TriageError("manifest_changed_during_read")
            if before.st_size > MAX_MANIFEST_BYTES:
                raise TriageError("manifest_too_large")
            payload = handle.read(MAX_MANIFEST_BYTES + 1)
            after = os.fstat(handle.fileno())
        if len(payload) > MAX_MANIFEST_BYTES:
            raise TriageError("manifest_too_large")
        if _identity(before) != _identity(after) or len(payload) != after.st_size:
            raise TriageError("manifest_changed_during_read")
        try:
            logical = os.stat("manifest.json", dir_fd=root_fd, follow_symlinks=False)
            current_root = root.lstat()
        except OSError:
            raise TriageError("manifest_or_root_changed") from None
        if (not stat.S_ISREG(logical.st_mode) or _identity(logical) != _identity(after)
                or stat.S_ISLNK(current_root.st_mode)
                or (current_root.st_dev, current_root.st_ino) !=
                    (opened_root.st_dev, opened_root.st_ino)):
            raise TriageError("manifest_or_root_changed")
        return payload
    except OSError:
        raise TriageError("unsafe_manifest_read") from None
    finally:
        os.close(root_fd)


def _valid_identifiers(value: Any, *, maximum: int) -> int:
    if type(value) is not list or len(value) > maximum:
        raise TriageError("inconsistent_unmatched_identifiers")
    seen = set()
    for item in value:
        if type(item) is not str or not item or item != item.strip() or item in seen:
            raise TriageError("inconsistent_unmatched_identifiers")
        seen.add(item)
    return len(value)


def triage(root: Path) -> dict[str, Any]:
    raw = _read_manifest(root)
    try:
        manifest = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TriageError("invalid_manifest_json") from None
    if (type(manifest) is not dict or type(manifest.get("schema_version")) is not int
            or manifest["schema_version"] != 1):
        raise TriageError("invalid_manifest_schema")
    counters: dict[str, int] = {}
    for key in COUNTS:
        count = manifest.get(key)
        if type(count) is not int or count < 0:
            raise TriageError("invalid_manifest_count")
        counters[key] = count
    if (counters["replay_count"] != counters["unique_replay_count"] + counters["duplicate_replay_count"]
            or counters["match_count"] > min(counters["unique_replay_count"], counters["report_count"])):
        raise TriageError("inconsistent_manifest_counts")
    unmatched_replays = _valid_identifiers(manifest.get("unmatched_replays"),
                                           maximum=counters["unique_replay_count"]-counters["match_count"])
    unmatched_reports = _valid_identifiers(manifest.get("unmatched_reports"),
                                           maximum=counters["report_count"])
    if unmatched_reports != counters["report_count"] - counters["match_count"]:
        raise TriageError("inconsistent_unmatched_reports")
    failures = manifest.get("failures")
    if type(failures) is not list:
        raise TriageError("invalid_failure_list")
    by_stage: Counter[str] = Counter()
    for failure in failures:
        if (type(failure) is not dict or any(type(failure.get(key)) is not str or
                not failure[key] for key in ("stage", "source", "error"))):
            raise TriageError("invalid_failure_record")
        stage = failure["stage"]
        if stage != stage.strip():
            raise TriageError("invalid_failure_record")
        by_stage[stage if stage in KNOWN_STAGES else "other_stage"] += 1
    return {
        "schema": SCHEMA,
        "diagnostic_only": True,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_counts": counters,
        "failure_records": len(failures),
        "by_stage": {key: by_stage[key] for key in sorted(by_stage)},
        "unmatched_valid_replays": unmatched_replays,
        "unmatched_reports": unmatched_reports,
        "source_paths_or_errors_emitted": False,
        "raw_inputs_validated": False,
        "full_dataset_integrity_proven": False,
        "live_runtime_observed": False,
        "training_authorized": False,
        "promotion_authorized": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("import_root", type=Path)
    parser.add_argument("--fail-on-failures", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = triage(args.import_root)
    except TriageError as exc:
        print(json.dumps({
            "schema": SCHEMA, "clean": False, "diagnostic_only": True,
            "training_authorized": False, "promotion_authorized": False,
            "reason": str(exc),
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 1 if args.fail_on_failures and report["failure_records"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
