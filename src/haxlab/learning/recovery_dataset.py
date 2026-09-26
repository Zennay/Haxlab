from __future__ import annotations

import argparse
import hashlib
import json
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


def build_dataset(trace_paths: Iterable[Path]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    inputs = []
    deduped: dict[str, dict[str, Any]] = {}

    for trace_path in sorted(Path(path) for path in trace_paths):
        header, rows = read_trace(trace_path)
        trace_sha = _sha256_file(trace_path)
        provenance = header.get("provenance") or {}
        inputs.append(
            {
                "path": str(trace_path),
                "sha256": trace_sha,
                "row_count": len(rows),
                "source_ref": header.get("source_ref"),
                "challenger_sha256": provenance.get("challenger_sha256"),
                "scenarios_sha256": provenance.get("scenarios_sha256"),
                "stadium_sha256": provenance.get("stadium_sha256"),
                "seed": (header.get("config") or {}).get("seed"),
            }
        )
        for row in rows:
            fingerprint = state_fingerprint(row)
            source = {
                "trace_sha256": trace_sha,
                "source_ref": header.get("source_ref"),
                "challenger_sha256": provenance.get("challenger_sha256"),
                "scenarios_sha256": provenance.get("scenarios_sha256"),
                "stadium_sha256": provenance.get("stadium_sha256"),
                "scenario_index": row.get("scenario_index"),
                "seed": row.get("seed"),
                "mode": row.get("mode"),
                "test_team_id": row.get("test_team_id"),
                "repeat_index": row.get("repeat_index"),
                "tick": row.get("tick"),
            }
            if fingerprint not in deduped:
                deduped[fingerprint] = {
                    "schema": DATASET_ROW_SCHEMA,
                    "state_fingerprint": fingerprint,
                    "split": split_for_fingerprint(fingerprint),
                    "role": row.get("role"),
                    "failure_types": sorted(set(row.get("failure_types") or [])),
                    "diagnostics": row.get("diagnostics") or {},
                    "auxiliary_targets": auxiliary_targets(row),
                    "recovery_window": row.get("recovery_window") or [],
                    "sources": [source],
                }
            else:
                existing = deduped[fingerprint]
                existing["failure_types"] = sorted(
                    set(existing.get("failure_types") or [])
                    | set(row.get("failure_types") or [])
                )
                if source not in existing["sources"]:
                    existing["sources"].append(source)

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
    manifest = {
        "schema": DATASET_SCHEMA,
        "human_replay_data_included": False,
        "state_fingerprint": "sha256(role + quantized-current-state-v1)",
        "quantization_decimals": 3,
        "split_policy": "sha256-prefix-mod100: train<80, validation<90, evaluation>=90",
        "input_traces": inputs,
        "input_trace_count": len(inputs),
        "raw_trace_rows": sum(item["row_count"] for item in inputs),
        "deduplicated_rows": len(deduped),
        "duplicate_rows_removed": sum(item["row_count"] for item in inputs) - len(deduped),
        "split_counts": {name: len(rows) for name, rows in split_rows.items()},
        "role_counts": dict(sorted(role_counts.items())),
        "failure_counts": dict(sorted(failure_counts.items())),
    }
    return manifest, split_rows


def write_dataset(output_root: Path, dataset_id: str, manifest: dict[str, Any], split_rows: dict[str, list[dict[str, Any]]]) -> Path:
    root = output_root / dataset_id
    root.mkdir(parents=True, exist_ok=True)
    output_hashes: dict[str, str] = {}
    for split in ("train", "validation", "evaluation"):
        path = root / f"{split}.jsonl"
        rendered = "".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
            for row in split_rows[split]
        )
        path.write_text(rendered, encoding="utf-8")
        output_hashes[path.name] = _sha256_file(path)
    final_manifest = dict(manifest)
    final_manifest["dataset_id"] = dataset_id
    final_manifest["output_sha256"] = output_hashes
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(final_manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a provenance-safe recovery dataset from Arena-v2 failure traces."
    )
    parser.add_argument("traces", nargs="+", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--dataset-id", required=True)
    args = parser.parse_args()

    manifest, split_rows = build_dataset(args.traces)
    manifest_path = write_dataset(
        args.output_root, args.dataset_id, manifest, split_rows
    )
    print(manifest_path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())