from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


TRACE_SCHEMA = "haxlab-recovery-trace-v1"
TRACE_ROW_SCHEMA = "haxlab-recovery-trace-row-v1"
DATASET_SCHEMA = "haxlab-recovery-dataset-v1"
DATASET_ROW_SCHEMA = "haxlab-recovery-dataset-row-v1"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical(value: Any, *, digits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {key: _canonical(value[key], digits=digits) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item, digits=digits) for item in value]
    return value


def _require_sha256(value: Any, label: str) -> str:
    rendered = str(value or "").lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} must be a 64-character SHA-256")
    return rendered


def state_fingerprint(row: dict[str, Any]) -> str:
    window = row.get("recovery_window") or []
    current = window[-1] if window else {}
    payload = {
        "role": row.get("role"),
        "features": current.get("features") or {},
        "player_position": current.get("player_position"),
        "ball_position": current.get("ball_position"),
    }
    rendered = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(rendered)


def rollout_group_fingerprint(
    header: dict[str, Any], row: dict[str, Any]
) -> str:
    provenance = header.get("provenance") or {}
    payload = {
        "source_replay_sha256": _require_sha256(
            provenance.get("source_replay_sha256"),
            "provenance.source_replay_sha256",
        ),
        "challenger_sha256": provenance.get("challenger_sha256"),
        "scenarios_sha256": provenance.get("scenarios_sha256"),
        "stadium_sha256": provenance.get("stadium_sha256"),
        "seed": row.get("seed", (header.get("config") or {}).get("seed")),
        "scenario_index": row.get("scenario_index"),
        "mode": row.get("mode"),
        "tested_role": row.get("tested_role"),
        "test_team_id": row.get("test_team_id"),
        "repeat_index": row.get("repeat_index"),
    }
    rendered = json.dumps(
        _canonical(payload), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256_bytes(rendered)


def split_for_fingerprint(fingerprint: str) -> str:
    bucket = int(fingerprint[:8], 16) % 100
    if bucket < 80:
        return "train"
    if bucket < 90:
        return "validation"
    return "evaluation"


def auxiliary_targets(row: dict[str, Any]) -> dict[str, Any]:
    diagnostics = row.get("diagnostics") or {}
    targets: dict[str, Any] = {}
    if diagnostics.get("role_target") is not None:
        targets["role_target"] = diagnostics["role_target"]
    team_shape = diagnostics.get("team_shape")
    if isinstance(team_shape, dict):
        targets["team_shape"] = {
            key: team_shape.get(key)
            for key in ("ordered", "span", "collapsed", "overstretched")
            if key in team_shape
        }
    return targets


def read_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        first = handle.readline()
        if not first:
            raise ValueError(f"empty recovery trace: {path}")
        header = json.loads(first)
        if header.get("schema") != TRACE_SCHEMA:
            raise ValueError(f"unsupported recovery trace schema: {path}")
        provenance = header.get("provenance") or {}
        _require_sha256(
            provenance.get("source_replay_sha256"),
            f"{path}: provenance.source_replay_sha256",
        )
        for line_no, line in enumerate(handle, start=2):
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("schema") != TRACE_ROW_SCHEMA:
                raise ValueError(
                    f"unsupported recovery row schema at {path}:{line_no}"
                )
            rows.append(row)
    return header, rows


def _source_record(
    *,
    header: dict[str, Any],
    row: dict[str, Any],
    trace_sha: str,
    group_fingerprint: str,
) -> dict[str, Any]:
    provenance = header.get("provenance") or {}
    return {
        "trace_sha256": trace_sha,
        "source_ref": header.get("source_ref"),
        "source_replay_sha256": _require_sha256(
            provenance.get("source_replay_sha256"),
            "provenance.source_replay_sha256",
        ),
        "challenger_sha256": provenance.get("challenger_sha256"),
        "scenarios_sha256": provenance.get("scenarios_sha256"),
        "stadium_sha256": provenance.get("stadium_sha256"),
        "rollout_group_fingerprint": group_fingerprint,
        "rollout_group_split": split_for_fingerprint(group_fingerprint),
        "scenario_index": row.get("scenario_index"),
        "tested_role": row.get("tested_role"),
        "seed": row.get("seed"),
        "mode": row.get("mode"),
        "test_team_id": row.get("test_team_id"),
        "repeat_index": row.get("repeat_index"),
        "tick": row.get("tick"),
    }


def build_dataset(
    trace_paths: Iterable[Path],
    *,
    forbidden_source_sha256s: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    forbidden = {value.lower() for value in (forbidden_source_sha256s or set())}
    inputs: list[dict[str, Any]] = []
    observations: list[
        tuple[str, str, str, dict[str, Any], dict[str, Any]]
    ] = []
    owner_group_by_state: dict[str, str] = {}
    rollout_groups: dict[str, str] = {}

    for trace_path in sorted(Path(path) for path in trace_paths):
        header, rows = read_trace(trace_path)
        trace_sha = _sha256_file(trace_path)
        provenance = header.get("provenance") or {}
        replay_sha = _require_sha256(
            provenance.get("source_replay_sha256"),
            f"{trace_path}: provenance.source_replay_sha256",
        )
        if replay_sha in forbidden:
            raise ValueError(
                f"forbidden recovery source replay SHA-256: {replay_sha}"
            )
        inputs.append(
            {
                "path": str(trace_path),
                "sha256": trace_sha,
                "row_count": len(rows),
                "source_ref": header.get("source_ref"),
                "source_replay_sha256": replay_sha,
                "challenger_sha256": provenance.get("challenger_sha256"),
                "scenarios_sha256": provenance.get("scenarios_sha256"),
                "stadium_sha256": provenance.get("stadium_sha256"),
                "seed": (header.get("config") or {}).get("seed"),
            }
        )
        for row in rows:
            state_fp = state_fingerprint(row)
            group_fp = rollout_group_fingerprint(header, row)
            group_split = split_for_fingerprint(group_fp)
            rollout_groups[group_fp] = group_split
            source = _source_record(
                header=header,
                row=row,
                trace_sha=trace_sha,
                group_fingerprint=group_fp,
            )
            observations.append((state_fp, group_fp, group_split, row, source))
            owner_group_by_state[state_fp] = min(
                group_fp,
                owner_group_by_state.get(state_fp, group_fp),
            )

    deduped: dict[str, dict[str, Any]] = {}
    cross_group_duplicates = 0
    cross_split_duplicates_resolved = 0
    cross_split_duplicate_observations_dropped = 0
    seen_groups_by_state: dict[str, set[str]] = {}
    seen_splits_by_state: dict[str, set[str]] = {}

    # First materialize each state from its deterministic canonical rollout owner.
    # This guarantees the recovery window/labels used for the emitted row come
    # from the same rollout group that owns the split.
    for state_fp, group_fp, group_split, row, source in observations:
        seen_groups_by_state.setdefault(state_fp, set()).add(group_fp)
        seen_splits_by_state.setdefault(state_fp, set()).add(group_split)
        owner_group = owner_group_by_state[state_fp]
        if group_fp != owner_group or state_fp in deduped:
            continue
        deduped[state_fp] = {
            "schema": DATASET_ROW_SCHEMA,
            "state_fingerprint": state_fp,
            "rollout_group_fingerprint": owner_group,
            "split": rollout_groups[owner_group],
            "role": row.get("role"),
            "failure_types": sorted(set(row.get("failure_types") or [])),
            "diagnostics": row.get("diagnostics") or {},
            "auxiliary_targets": auxiliary_targets(row),
            "recovery_window": row.get("recovery_window") or [],
            "sources": [source],
        }

    # Merge only duplicate observations that belong to the owner's split.
    # Cross-split duplicates are audit-counted but never attached to an emitted
    # row, so no rollout group can appear in more than one dataset split.
    for state_fp, group_fp, group_split, row, source in observations:
        existing = deduped[state_fp]
        if group_split != existing["split"]:
            cross_split_duplicate_observations_dropped += 1
            continue
        existing["failure_types"] = sorted(
            set(existing.get("failure_types") or [])
            | set(row.get("failure_types") or [])
        )
        if source not in existing["sources"]:
            existing["sources"].append(source)

    for state_fp, groups in seen_groups_by_state.items():
        if len(groups) > 1:
            cross_group_duplicates += 1
        if len(seen_splits_by_state[state_fp]) > 1:
            cross_split_duplicates_resolved += 1

    split_rows = {"train": [], "validation": [], "evaluation": []}
    for fingerprint in sorted(deduped):
        row = deduped[fingerprint]
        split_rows[row["split"]].append(row)

    role_counts = Counter(row.get("role") for row in deduped.values())
    failure_counts = Counter(
        failure
        for row in deduped.values()
        for failure in row.get("failure_types") or []
    )
    rollout_split_counts = Counter(rollout_groups.values())
    manifest = {
        "schema": DATASET_SCHEMA,
        "human_replay_data_included": False,
        "state_fingerprint": "sha256(role + quantized-current-state-v1)",
        "quantization_decimals": 3,
        "rollout_group_fingerprint": (
            "sha256(source-replay + challenger + scenario/stadium + seed + "
            "scenario-index + mode + tested-role + side + repeat)"
        ),
        "split_policy": (
            "rollout-group-sha256-prefix-mod100: train<80, validation<90, "
            "evaluation>=90; global state dedupe uses the lexicographically "
            "smallest rollout-group fingerprint as canonical owner; duplicate "
            "observations from a different split are dropped fail-closed"
        ),
        "dedupe_owner_policy": "lexicographically-smallest-rollout-group-fingerprint",
        "forbidden_source_sha256_count": len(forbidden),
        "input_traces": inputs,
        "input_trace_count": len(inputs),
        "raw_trace_rows": sum(item["row_count"] for item in inputs),
        "rollout_group_count": len(rollout_groups),
        "rollout_group_split_counts": dict(sorted(rollout_split_counts.items())),
        "deduplicated_rows": len(deduped),
        "duplicate_rows_removed": (
            sum(item["row_count"] for item in inputs) - len(deduped)
        ),
        "cross_group_duplicate_states": cross_group_duplicates,
        "cross_split_duplicate_states_resolved": cross_split_duplicates_resolved,
        "cross_split_duplicate_observations_dropped": (
            cross_split_duplicate_observations_dropped
        ),
        "split_counts": {name: len(rows) for name, rows in split_rows.items()},
        "role_counts": dict(sorted(role_counts.items())),
        "failure_counts": dict(sorted(failure_counts.items())),
    }
    return manifest, split_rows


def _render_dataset_files(
    dataset_id: str,
    manifest: dict[str, Any],
    split_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, bytes]:
    rendered: dict[str, bytes] = {}
    output_hashes: dict[str, str] = {}
    for split in ("train", "validation", "evaluation"):
        name = f"{split}.jsonl"
        payload = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in split_rows[split]
        ).encode("utf-8")
        rendered[name] = payload
        output_hashes[name] = _sha256_bytes(payload)
    final_manifest = dict(manifest)
    final_manifest["dataset_id"] = dataset_id
    final_manifest["output_sha256"] = output_hashes
    manifest_bytes = (
        json.dumps(final_manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    rendered["manifest.json"] = manifest_bytes
    return rendered


def _directory_matches(root: Path, expected: dict[str, bytes]) -> bool:
    if not root.is_dir():
        return False
    actual_names = sorted(path.name for path in root.iterdir() if path.is_file())
    if actual_names != sorted(expected):
        return False
    return all((root / name).read_bytes() == payload for name, payload in expected.items())


def write_dataset(
    output_root: Path,
    dataset_id: str,
    manifest: dict[str, Any],
    split_rows: dict[str, list[dict[str, Any]]],
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    root = output_root / dataset_id
    rendered = _render_dataset_files(dataset_id, manifest, split_rows)

    if root.exists():
        if _directory_matches(root, rendered):
            return root / "manifest.json"
        raise FileExistsError(
            f"dataset-id already exists with different content: {dataset_id}"
        )

    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{dataset_id}.", dir=str(output_root))
    )
    try:
        for name, payload in rendered.items():
            (temp_root / name).write_bytes(payload)
        try:
            temp_root.rename(root)
        except FileExistsError:
            if _directory_matches(root, rendered):
                shutil.rmtree(temp_root)
                return root / "manifest.json"
            raise FileExistsError(
                f"dataset-id raced with different content: {dataset_id}"
            )
    except Exception:
        if temp_root.exists():
            shutil.rmtree(temp_root)
        raise
    return root / "manifest.json"


def read_sha256_set(path: Path) -> set[str]:
    values: set[str] = set()
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        value = raw.strip().lower()
        if not value:
            continue
        values.add(_require_sha256(value, f"{path}:{line_no}"))
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a provenance-safe recovery dataset from Arena-v2 failure traces."
    )
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument(
        "--forbidden-source-sha256-file",
        type=Path,
        default=None,
        help=(
            "Fail closed if any recovery trace source replay SHA-256 appears "
            "in this reserved/evaluation source list."
        ),
    )
    args = parser.parse_args()

    forbidden = (
        read_sha256_set(args.forbidden_source_sha256_file)
        if args.forbidden_source_sha256_file is not None
        else set()
    )
    manifest, split_rows = build_dataset(
        args.traces,
        forbidden_source_sha256s=forbidden,
    )
    manifest_path = write_dataset(
        args.output_root, args.dataset_id, manifest, split_rows
    )
    print(manifest_path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
