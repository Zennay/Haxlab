from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.runtime.state import CURRENT_ANALYZER_VERSION, RuntimeState


AUDIT_SCHEMA = "haxlab-analysis-artifact-audit-v1"


def _expected_schema_version(analyzer_version: str) -> int | None:
    prefix = "state-pass-v"
    if not analyzer_version.startswith(prefix):
        return None
    suffix = analyzer_version[len(prefix):]
    return int(suffix) if suffix.isdigit() else None


def _is_native_int(value: object) -> bool:
    return type(value) is int


def _expected_output_path(
    derived_root: Path,
    analyzer_version: str,
    sha256: str,
) -> Path:
    return (
        derived_root
        / analyzer_version
        / sha256[:2]
        / sha256[2:4]
        / f"{sha256}.json"
    )


def audit_analysis_artifacts(
    state: RuntimeState,
    *,
    derived_root: Path,
    analyzer_version: str = CURRENT_ANALYZER_VERSION,
    max_issues: int = 100,
) -> dict[str, object]:
    """Reconcile successful analysis ledger rows with their derived JSON artifacts."""

    rows = state.connection.execute(
        """
        SELECT
            a.sha256,
            a.output_path,
            a.sampled_state_count,
            a.player_count,
            a.raw_event_count,
            a.tick_count,
            p.status AS processing_status,
            p.total_frames AS processed_total_frames
        FROM replay_analysis_versions AS a
        LEFT JOIN replay_processing AS p ON p.sha256 = a.sha256
        WHERE a.analyzer_version = ? AND a.status = 'ok'
        ORDER BY a.sha256
        """,
        (analyzer_version,),
    ).fetchall()

    checked_records = 0
    existing_files = 0
    missing_files = 0
    path_mismatches = 0
    symlink_entries = 0
    invalid_json = 0
    payload_mismatches = 0
    row_objects_with_issues = 0
    untracked_artifacts = 0
    issues: list[dict[str, object]] = []
    expected_paths: set[Path] = set()
    expected_schema_version = _expected_schema_version(analyzer_version)

    for row in rows:
        checked_records += 1
        sha256 = str(row["sha256"])
        output_path = row["output_path"]
        expected_path = _expected_output_path(derived_root, analyzer_version, sha256)
        expected_paths.add(expected_path)
        reasons: list[str] = []
        payload: dict[str, Any] | None = None

        if not output_path:
            missing_files += 1
            reasons.append("output_path_missing")
            path = expected_path
        else:
            path = Path(str(output_path))
            if path != expected_path:
                path_mismatches += 1
                reasons.append(
                    f"output_path_mismatch:expected={expected_path}:actual={path}"
                )

            if path.is_symlink():
                symlink_entries += 1
                reasons.append("symlink_not_allowed")
            elif not path.is_file():
                missing_files += 1
                reasons.append("analysis_artifact_missing")
            else:
                existing_files += 1
                try:
                    raw_payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    invalid_json += 1
                    reasons.append(f"invalid_json:{type(exc).__name__}")
                else:
                    if not isinstance(raw_payload, dict):
                        payload_mismatches += 1
                        reasons.append("payload_not_object")
                    else:
                        payload = raw_payload

        if payload is not None:
            payload_reasons: list[str] = []

            if expected_schema_version is not None:
                schema_version = payload.get("schemaVersion")
                if (
                    not _is_native_int(schema_version)
                    or schema_version != expected_schema_version
                ):
                    payload_reasons.append(
                        "schema_version_mismatch:"
                        f"expected={expected_schema_version}:actual={schema_version!r}"
                    )

            if row["processing_status"] != "ok":
                payload_reasons.append(
                    f"processing_status_mismatch:actual={row['processing_status']!r}"
                )

            total_frames = payload.get("totalFrames")
            expected_total_frames = row["processed_total_frames"]
            if (
                expected_total_frames is None
                or not _is_native_int(total_frames)
                or total_frames != int(expected_total_frames)
            ):
                payload_reasons.append(
                    "total_frames_mismatch:"
                    f"expected={expected_total_frames!r}:actual={total_frames!r}"
                )

            raw_event_count = payload.get("rawEventCount")
            expected_raw_events = row["raw_event_count"]
            if (
                expected_raw_events is None
                or not _is_native_int(raw_event_count)
                or raw_event_count != int(expected_raw_events)
            ):
                payload_reasons.append(
                    "raw_event_count_mismatch:"
                    f"expected={expected_raw_events!r}:actual={raw_event_count!r}"
                )

            players = payload.get("players")
            expected_players = row["player_count"]
            if not isinstance(players, list) or expected_players is None:
                payload_reasons.append(
                    "player_count_mismatch:"
                    f"expected={expected_players!r}:actual="
                    f"{len(players) if isinstance(players, list) else players!r}"
                )
            elif len(players) != int(expected_players):
                payload_reasons.append(
                    "player_count_mismatch:"
                    f"expected={expected_players}:actual={len(players)}"
                )

            simulation = payload.get("simulation")
            if not isinstance(simulation, dict):
                payload_reasons.append("simulation_not_object")
            else:
                sampled_states = simulation.get("sampledStateCount")
                expected_samples = row["sampled_state_count"]
                if (
                    expected_samples is None
                    or not _is_native_int(sampled_states)
                    or sampled_states != int(expected_samples)
                ):
                    payload_reasons.append(
                        "sampled_state_count_mismatch:"
                        f"expected={expected_samples!r}:actual={sampled_states!r}"
                    )

                frames_advanced = simulation.get("framesAdvanced")
                expected_ticks = row["tick_count"]
                if (
                    expected_ticks is None
                    or not _is_native_int(frames_advanced)
                    or frames_advanced != int(expected_ticks)
                ):
                    payload_reasons.append(
                        "frames_advanced_mismatch:"
                        f"expected={expected_ticks!r}:actual={frames_advanced!r}"
                    )

            if payload_reasons:
                payload_mismatches += 1
                reasons.extend(payload_reasons)

        if reasons:
            row_objects_with_issues += 1
            if len(issues) < max(0, max_issues):
                issues.append(
                    {
                        "sha256": sha256,
                        "output_path": str(output_path) if output_path else None,
                        "reasons": reasons,
                    }
                )

    analysis_root = derived_root / analyzer_version
    if analysis_root.is_dir():
        for artifact_path in sorted(analysis_root.rglob("*.json")):
            if artifact_path.name.startswith("_"):
                continue
            if artifact_path not in expected_paths:
                untracked_artifacts += 1
                if len(issues) < max(0, max_issues):
                    issues.append(
                        {
                            "sha256": None,
                            "output_path": str(artifact_path),
                            "reasons": ["untracked_analysis_artifact"],
                        }
                    )

    objects_with_issues = row_objects_with_issues + untracked_artifacts

    return {
        "schema": AUDIT_SCHEMA,
        "audited_at": datetime.now(timezone.utc).isoformat(),
        "analyzer_version": analyzer_version,
        "ok": objects_with_issues == 0,
        "checked_records": checked_records,
        "valid_objects": checked_records - row_objects_with_issues,
        "existing_files": existing_files,
        "missing_files": missing_files,
        "path_mismatches": path_mismatches,
        "symlink_entries": symlink_entries,
        "invalid_json": invalid_json,
        "payload_mismatches": payload_mismatches,
        "row_objects_with_issues": row_objects_with_issues,
        "untracked_artifacts": untracked_artifacts,
        "objects_with_issues": objects_with_issues,
        "issues": issues,
        "issues_truncated": objects_with_issues > len(issues),
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-audit-analysis")
    parser.add_argument(
        "--state-db",
        type=Path,
        default=Path("/var/lib/haxlab/state/haxlab.sqlite3"),
    )
    parser.add_argument(
        "--derived-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived"),
    )
    parser.add_argument(
        "--analyzer-version",
        default=CURRENT_ANALYZER_VERSION,
    )
    parser.add_argument("--max-issues", type=int, default=100)
    args = parser.parse_args()

    with RuntimeState(args.state_db) as state:
        report = audit_analysis_artifacts(
            state,
            derived_root=args.derived_root,
            analyzer_version=args.analyzer_version,
            max_issues=args.max_issues,
        )

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["ok"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
