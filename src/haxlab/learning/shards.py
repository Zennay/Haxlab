from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.hashing import sha256_file
from haxlab.learning.selector import MANIFEST_SCHEMA


INDEX_SCHEMA = "haxlab-imitation-shard-index-v2"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _native_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def _verify_analysis_provenance(entry: dict[str, Any]) -> None:
    expected_sha = entry.get("analysis_sha256")
    if expected_sha is None:
        return

    expected_sha = str(expected_sha)
    if (
        len(expected_sha) != 64
        or expected_sha != expected_sha.lower()
        or any(char not in "0123456789abcdef" for char in expected_sha)
    ):
        raise ValueError("invalid analysis_sha256 provenance")

    analysis_path_value = entry.get("analysis_path")
    if not analysis_path_value:
        raise ValueError("analysis_path required when analysis_sha256 is present")

    analysis_path = Path(str(analysis_path_value))
    if analysis_path.is_symlink():
        raise ValueError(f"analysis artifact symlink not allowed: {analysis_path}")
    if not analysis_path.is_file():
        raise FileNotFoundError(f"analysis artifact missing: {analysis_path}")

    expected_size = entry.get("analysis_size_bytes")
    if expected_size is not None:
        if not _native_nonnegative_int(expected_size):
            raise ValueError("analysis_size_bytes must be a non-negative integer")
        actual_size = analysis_path.stat().st_size
        if actual_size != expected_size:
            raise ValueError(
                "analysis artifact size mismatch: "
                f"expected={expected_size}:actual={actual_size}"
            )

    actual_sha = sha256_file(analysis_path)
    if actual_sha != expected_sha:
        raise ValueError(
            "analysis artifact sha256 mismatch: "
            f"expected={expected_sha}:actual={actual_sha}"
        )


def _extraction_input_fingerprint(
    *,
    replay_sha256: str,
    selected_player_map: dict[str, str],
    sample_every_ticks: int,
    analysis_sha256: object,
    example_weight: float,
) -> str:
    payload = {
        "replay_sha256": replay_sha256,
        "selected_player_map": dict(sorted(selected_player_map.items())),
        "sample_every_ticks": sample_every_ticks,
        "analysis_sha256": analysis_sha256,
        "example_weight": example_weight,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _extract_one(
    entry: dict[str, Any],
    *,
    node_script: Path,
    output_dir: Path,
    sample_every_ticks: int,
    timeout_seconds: int,
    force: bool,
) -> dict[str, Any]:
    replay_sha256 = str(entry["replay_sha256"])
    shard_path = output_dir / f"{replay_sha256}.f32.gz"
    meta_path = output_dir / f"{replay_sha256}.meta.json"

    selected_players = list(entry.get("selected_players") or [])
    if not selected_players:
        raise ValueError(f"{replay_sha256}: no active selected replay players")

    selected_player_map = {
        str(int(row["replay_player_id"])): str(row["identity"])
        for row in selected_players
    }
    example_weight = float(entry.get("example_weight", 1.0))

    raw_path = Path(str(entry["raw_path"]))
    if not raw_path.is_file():
        raise FileNotFoundError(f"{replay_sha256}: raw replay missing: {raw_path}")

    _verify_analysis_provenance(entry)
    input_fingerprint = _extraction_input_fingerprint(
        replay_sha256=replay_sha256,
        selected_player_map=selected_player_map,
        sample_every_ticks=sample_every_ticks,
        analysis_sha256=entry.get("analysis_sha256"),
        example_weight=example_weight,
    )

    if not force and shard_path.exists() and meta_path.exists():
        try:
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
        if (
            previous.get("schema") == "haxlab-imitation-extract-summary-v2"
            and int(previous.get("sampleEveryTicks", 0))
            == sample_every_ticks
            and int(previous.get("samples", 0)) > 0
            and previous.get("input_fingerprint") == input_fingerprint
        ):
            return {**previous, "status": "cached"}

    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "node",
        str(node_script),
        str(raw_path),
        str(shard_path),
        json.dumps(
            selected_player_map,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        str(sample_every_ticks),
    ]

    completed = subprocess.run(
        command,
        cwd=node_script.parent.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"{replay_sha256}: extractor_exit_{completed.returncode}:"
            f"{completed.stderr.strip()[-4000:]}"
        )

    try:
        summary = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{replay_sha256}: invalid extractor JSON: {exc}; "
            f"stdout={completed.stdout[-1000:]!r}; "
            f"stderr={completed.stderr[-1000:]!r}"
        ) from exc

    summary.update(
        {
            "replay_sha256": replay_sha256,
            "raw_path": str(raw_path),
            "shard_path": str(shard_path),
            "selected_player_ids": sorted(set(selected_player_map.values())),
            "selected_players": selected_players,
            "example_weight": example_weight,
            "analysis_path": entry.get("analysis_path"),
            "analysis_sha256": entry.get("analysis_sha256"),
            "input_fingerprint": input_fingerprint,
            "status": "ok",
        }
    )
    _atomic_json(meta_path, summary)
    return summary


def build_shards(
    *,
    manifest_path: Path,
    split: str,
    output_root: Path,
    node_script: Path,
    sample_every_ticks: int = 6,
    workers: int = 2,
    limit: int | None = None,
    timeout_seconds: int = 180,
    force: bool = False,
) -> dict[str, Any]:
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(
            f"Unsupported manifest schema: {manifest.get('schema')!r}; "
            f"expected {MANIFEST_SCHEMA!r}"
        )
    if split not in {"train", "holdout"}:
        raise ValueError("split must be 'train' or 'holdout'")

    source_key = f"{split}_replays"
    entries = list(manifest.get(source_key) or [])
    if limit is not None:
        entries = entries[: max(0, limit)]

    output_dir = output_root / split
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(
                _extract_one,
                entry,
                node_script=node_script,
                output_dir=output_dir,
                sample_every_ticks=max(1, sample_every_ticks),
                timeout_seconds=max(30, timeout_seconds),
                force=force,
            ): entry
            for entry in entries
        }

        for future in as_completed(future_map):
            entry = future_map[future]
            try:
                results.append(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "replay_sha256": str(entry.get("replay_sha256")),
                        "error": str(exc),
                    }
                )

    results.sort(key=lambda row: row["replay_sha256"])
    failures.sort(key=lambda row: row["replay_sha256"])

    index = {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "manifest_size_bytes": len(manifest_bytes),
        "manifest_schema": manifest.get("schema"),
        "analysis_version": manifest.get("analysis_version"),
        "split": split,
        "sample_every_ticks": max(1, sample_every_ticks),
        "requested_replays": len(entries),
        "successful_replays": len(results),
        "failed_replays": len(failures),
        "samples": sum(int(row.get("samples", 0)) for row in results),
        "compressed_bytes": sum(
            int(row.get("compressedBytes", 0)) for row in results
        ),
        "selected_players_seen": sum(
            int(row.get("selectedPlayersSeen", 0)) for row in results
        ),
        "unknown_input_samples_skipped": sum(
            int(row.get("skippedUnknownInput", 0)) for row in results
        ),
        "entries": results,
        "failures": failures,
    }
    _atomic_json(output_dir / "_index.json", index)
    return index


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-build-shards")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/"
            "human-imitation-state-pass-v4.json"
        ),
    )
    parser.add_argument(
        "--split",
        choices=["train", "holdout"],
        default="train",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/shards/state-pass-v4"
        ),
    )
    parser.add_argument(
        "--node-script",
        type=Path,
        default=Path("/opt/haxlab/tools/extract_imitation.js"),
    )
    parser.add_argument("--sample-every-ticks", type=int, default=6)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    index = build_shards(
        manifest_path=args.manifest,
        split=args.split,
        output_root=args.output_root,
        node_script=args.node_script,
        sample_every_ticks=max(1, args.sample_every_ticks),
        workers=max(1, args.workers),
        limit=args.limit,
        timeout_seconds=max(30, args.timeout_seconds),
        force=args.force,
    )

    print(
        json.dumps(
            {
                key: index[key]
                for key in (
                    "split",
                    "requested_replays",
                    "successful_replays",
                    "failed_replays",
                    "samples",
                    "compressed_bytes",
                    "selected_players_seen",
                    "unknown_input_samples_skipped",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(
        "index:",
        args.output_root / args.split / "_index.json",
    )

    if index["failed_replays"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
