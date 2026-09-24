from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from haxlab.ingestion.discovery import discover_replays
from haxlab.ingestion.discord_export import read_discord_exports
from haxlab.ingestion.matcher import match_replays_to_reports
from haxlab.models import ImportFailure, ImportManifest, MatchReport, ReplayFile
from haxlab.replay.validation import validate_replay_basic


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _write_jsonl(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _relative(path: str, root: Path) -> str:
    candidate = Path(path)
    try:
        return str(candidate.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(candidate)


def _report_dict(report: MatchReport) -> dict[str, Any]:
    value = asdict(report)
    value["attachments"] = [asdict(item) for item in report.attachments]
    return value


def _replay_dict(replay: ReplayFile, root: Path) -> dict[str, Any]:
    validation = validate_replay_basic(Path(replay.path))
    return {
        "sha256": replay.sha256,
        "source_path": _relative(replay.path, root),
        "file_name": replay.file_name,
        "size_bytes": replay.size_bytes,
        "basic_validation": {
            "valid": validation.valid,
            "reasons": list(validation.reasons),
        },
    }


def run_import(
    export_root: Path,
    output_root: Path,
    *,
    minimum_match_confidence: float = 0.65,
) -> ImportManifest:
    """Build deterministic derived inventory from an immutable raw export."""
    export_root = export_root.resolve()
    output_root = output_root.resolve()

    if export_root == output_root or output_root.is_relative_to(export_root):
        raise ValueError("output_root must be outside the immutable raw export directory")

    inventory = discover_replays(export_root)
    reports, failures = read_discord_exports(export_root)

    valid_replays: list[ReplayFile] = []
    for replay in inventory.unique_files:
        validation = validate_replay_basic(Path(replay.path))
        if validation.valid:
            valid_replays.append(replay)
        else:
            failures.append(
                ImportFailure(
                    source=_relative(replay.path, export_root),
                    stage="hbr2_validation",
                    error=",".join(validation.reasons),
                )
            )

    matches = match_replays_to_reports(
        valid_replays,
        reports,
        minimum_confidence=minimum_match_confidence,
    )

    reports_by_id = {report.message_id: report for report in reports}
    replays_by_hash = {replay.sha256: replay for replay in inventory.unique_files}

    matched_hashes = {match.replay_sha256 for match in matches}
    matched_message_ids = {
        match.message_id for match in matches if match.message_id is not None
    }

    canonical_matches: list[dict[str, Any]] = []
    for match in matches:
        replay = replays_by_hash[match.replay_sha256]
        report = reports_by_id.get(match.message_id or "")
        match_id = (
            report.report_id
            if report is not None and report.report_id
            else f"hbr2:{replay.sha256[:16]}"
        )

        canonical_matches.append(
            {
                "schema_version": 1,
                "match_id": match_id,
                "replay_sha256": replay.sha256,
                "replay_source_path": _relative(replay.path, export_root),
                "source_message_id": match.message_id,
                "match_confidence": match.confidence,
                "match_reasons": list(match.reasons),
                "report": _report_dict(report) if report is not None else None,
            }
        )

    canonical_matches.sort(key=lambda item: item["match_id"])

    manifest = ImportManifest(
        replay_count=len(inventory.all_files),
        unique_replay_count=len(inventory.unique_files),
        duplicate_replay_count=len(inventory.all_files) - len(inventory.unique_files),
        report_count=len(reports),
        match_count=len(matches),
        unmatched_replays=sorted(
            _relative(replay.path, export_root)
            for replay in valid_replays
            if replay.sha256 not in matched_hashes
        ),
        unmatched_reports=sorted(
            report.message_id
            for report in reports
            if report.message_id not in matched_message_ids
        ),
        failures=failures,
    )

    output_root.mkdir(parents=True, exist_ok=True)

    _write_json(
        output_root / "replays.json",
        [_replay_dict(replay, export_root) for replay in inventory.unique_files],
    )
    _write_json(
        output_root / "duplicates.json",
        {
            digest: [_relative(path, export_root) for path in paths]
            for digest, paths in sorted(inventory.duplicate_paths_by_hash.items())
        },
    )
    _write_json(
        output_root / "reports.json",
        [_report_dict(report) for report in reports],
    )
    _write_jsonl(output_root / "matches.jsonl", canonical_matches)
    _write_json(output_root / "manifest.json", manifest.as_dict())

    return manifest
