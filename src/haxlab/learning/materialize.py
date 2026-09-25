from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.learning.shards import _atomic_json, build_shards


SUMMARY_SCHEMA = "haxlab-imitation-materialization-v1"


def materialize(
    *,
    manifest_path: Path,
    output_root: Path,
    node_script: Path,
    sample_every_ticks: int = 6,
    workers: int = 4,
    timeout_seconds: int = 180,
    force: bool = False,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()

    train = build_shards(
        manifest_path=manifest_path,
        split="train",
        output_root=output_root,
        node_script=node_script,
        sample_every_ticks=sample_every_ticks,
        workers=workers,
        timeout_seconds=timeout_seconds,
        force=force,
    )
    holdout = build_shards(
        manifest_path=manifest_path,
        split="holdout",
        output_root=output_root,
        node_script=node_script,
        sample_every_ticks=sample_every_ticks,
        workers=workers,
        timeout_seconds=timeout_seconds,
        force=force,
    )

    summary = {
        "schema": SUMMARY_SCHEMA,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "analysis_version": train.get("analysis_version"),
        "sample_every_ticks": sample_every_ticks,
        "workers": workers,
        "train": {
            key: train.get(key)
            for key in (
                "requested_replays",
                "successful_replays",
                "failed_replays",
                "samples",
                "compressed_bytes",
                "selected_players_seen",
                "unknown_input_samples_skipped",
            )
        },
        "holdout": {
            key: holdout.get(key)
            for key in (
                "requested_replays",
                "successful_replays",
                "failed_replays",
                "samples",
                "compressed_bytes",
                "selected_players_seen",
                "unknown_input_samples_skipped",
            )
        },
    }
    summary["failed_replays"] = int(train["failed_replays"]) + int(
        holdout["failed_replays"]
    )
    summary["samples"] = int(train["samples"]) + int(holdout["samples"])
    summary["compressed_bytes"] = int(train["compressed_bytes"]) + int(
        holdout["compressed_bytes"]
    )
    _atomic_json(output_root / "_complete.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-materialize-shards")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/"
            "human-imitation-state-pass-v4.json"
        ),
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
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    summary = materialize(
        manifest_path=args.manifest,
        output_root=args.output_root,
        node_script=args.node_script,
        sample_every_ticks=max(1, args.sample_every_ticks),
        workers=max(1, args.workers),
        timeout_seconds=max(30, args.timeout_seconds),
        force=args.force,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if int(summary["failed_replays"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
