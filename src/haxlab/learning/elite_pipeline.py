from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from haxlab.evaluation.elite_gate import decide_elite_live_gate
from haxlab.learning.elite import train_elite_policy
from haxlab.learning.elite_selector import build_elite_manifest
from haxlab.learning.elite_shards import build_elite_shards


def _write_progress(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "haxlab-elite-progress-v1",
                **payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def run_elite_pipeline(
    *,
    analysis_root: Path,
    leaderboard_path: Path,
    raw_root: Path,
    work_root: Path,
    node_script: Path,
    aliases_path: Path | None = None,
    top_fraction_per_role: float = 0.20,
    min_players_per_role: int = 4,
    min_matches: int = 30,
    min_minutes: float = 120.0,
    max_uncertainty: float = 1.5,
    shard_workers: int = 2,
    sample_every_ticks: int = 6,
    window: int = 8,
    sequence_stride: int = 1,
    hidden_dim: int = 128,
    hidden_dim_2: int = 96,
    epochs: int = 5,
    batch_size: int = 2048,
    learning_rate: float = 8e-4,
    l2: float = 1e-5,
    seed: int = 1337,
    replay_limit: int | None = None,
    train_replay_limit: int | None = None,
    validation_replay_limit: int | None = None,
    holdout_replay_limit: int | None = None,
    force_shards: bool = False,
) -> dict[str, Any]:
    work_root.mkdir(parents=True, exist_ok=True)
    manifest_path = work_root / "elite-4v4-manifest.json"
    shards_root = work_root / "shards"
    model_dir = work_root / "model"
    progress_path = work_root / "progress.json"
    _write_progress(
        progress_path,
        {
            "phase": "manifest",
            "work_root": str(work_root),
        },
    )

    manifest = build_elite_manifest(
        analysis_root=analysis_root,
        leaderboard_path=leaderboard_path,
        raw_root=raw_root,
        aliases_path=aliases_path,
        top_fraction_per_role=top_fraction_per_role,
        min_players_per_role=min_players_per_role,
        min_matches=min_matches,
        min_minutes=min_minutes,
        max_uncertainty=max_uncertainty,
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_progress(
        progress_path,
        {
            "phase": "shards",
            "current_split": "train",
            "selection": manifest["stats"],
        },
    )

    indexes: dict[str, dict[str, Any]] = {}
    index_paths: dict[str, Path] = {}
    split_limits = {
        "train": train_replay_limit if train_replay_limit is not None else replay_limit,
        "validation": (
            validation_replay_limit
            if validation_replay_limit is not None
            else replay_limit
        ),
        "holdout": (
            holdout_replay_limit
            if holdout_replay_limit is not None
            else replay_limit
        ),
    }
    for split in ("train", "validation", "holdout"):
        index = build_elite_shards(
            manifest_path=manifest_path,
            split=split,
            output_root=shards_root,
            node_script=node_script,
            sample_every_ticks=sample_every_ticks,
            workers=shard_workers,
            limit=split_limits[split],
            force=force_shards,
        )
        indexes[split] = index
        index_paths[split] = shards_root / split / "_index.json"
        _write_progress(
            progress_path,
            {
                "phase": "shards",
                "completed_split": split,
                "successful_replays": index["successful_replays"],
                "failed_replays": index["failed_replays"],
                "samples": index["samples"],
                "role_sample_counts": index.get("role_sample_counts", {}),
            },
        )
        if index["failed_replays"]:
            raise RuntimeError(
                f"{split} shard extraction failed for "
                f"{index['failed_replays']} replay(s)"
            )
        if int(index["samples"]) <= 0:
            raise RuntimeError(f"{split} produced zero elite samples")

    _write_progress(
        progress_path,
        {
            "phase": "training_start",
            "splits": {
                split: {
                    "replays": indexes[split]["successful_replays"],
                    "samples": indexes[split]["samples"],
                }
                for split in ("train", "validation", "holdout")
            },
        },
    )

    model = train_elite_policy(
        train_index_path=index_paths["train"],
        validation_index_path=index_paths["validation"],
        holdout_index_path=index_paths["holdout"],
        output_dir=model_dir,
        window=window,
        sequence_stride=sequence_stride,
        hidden_dim=hidden_dim,
        hidden_dim_2=hidden_dim_2,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        l2=l2,
        seed=seed,
        progress_path=progress_path,
    )

    live_gate = decide_elite_live_gate(model)

    summary = {
        "manifest_path": str(manifest_path),
        "shards_root": str(shards_root),
        "model_dir": str(model_dir),
        "selection": manifest["stats"],
        "splits": {
            split: {
                "replays": indexes[split]["successful_replays"],
                "samples": indexes[split]["samples"],
                "role_sample_counts": indexes[split].get("role_sample_counts", {}),
            }
            for split in ("train", "validation", "holdout")
        },
        "best_epoch": model["training"]["best_epoch"],
        "kick_threshold": model["training"]["calibrated_kick_threshold"],
        "validation": model["final_validation"],
        "frozen_holdout": model["final_holdout"],
        "live_test_gate": {
            "eligible_for_live_test": live_gate.eligible_for_live_test,
            "reasons": list(live_gate.reasons),
            "checks": live_gate.checks,
        },
    }
    (work_root / "pipeline-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_progress(
        progress_path,
        {
            "phase": "pipeline_complete",
            "summary_path": str(work_root / "pipeline-summary.json"),
            "live_test_gate": summary["live_test_gate"],
            "best_epoch": summary["best_epoch"],
        },
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-pipeline")
    parser.add_argument(
        "--analysis-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/state-pass-v4"),
    )
    parser.add_argument(
        "--leaderboard",
        type=Path,
        default=Path("/var/lib/haxlab/derived/leaderboards/state-pass-v4.json"),
    )
    parser.add_argument(
        "--raw-root",
        type=Path,
        default=Path("/var/lib/haxlab/raw/replays"),
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path("/var/lib/haxlab/derived/training/elite-player-v01"),
    )
    parser.add_argument(
        "--node-script",
        type=Path,
        default=Path("/opt/haxlab/tools/extract_elite_imitation.js"),
    )
    parser.add_argument(
        "--aliases",
        type=Path,
        default=Path("/opt/haxlab/configs/player_aliases.json"),
    )
    parser.add_argument("--top-fraction-per-role", type=float, default=0.20)
    parser.add_argument("--min-players-per-role", type=int, default=4)
    parser.add_argument("--min-matches", type=int, default=30)
    parser.add_argument("--min-minutes", type=float, default=120.0)
    parser.add_argument("--max-uncertainty", type=float, default=1.5)
    parser.add_argument("--shard-workers", type=int, default=2)
    parser.add_argument("--sample-every-ticks", type=int, default=6)
    parser.add_argument("--window", type=int, default=8)
    parser.add_argument("--sequence-stride", type=int, default=1)
    parser.add_argument("--hidden-dim", type=int, default=128)
    parser.add_argument("--hidden-dim-2", type=int, default=96)
    parser.add_argument("--epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--learning-rate", type=float, default=8e-4)
    parser.add_argument("--l2", type=float, default=1e-5)
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument(
        "--replay-limit",
        type=int,
        default=None,
        help="Legacy cap applied to all splits unless a split-specific cap is set.",
    )
    parser.add_argument("--train-replay-limit", type=int, default=None)
    parser.add_argument("--validation-replay-limit", type=int, default=None)
    parser.add_argument("--holdout-replay-limit", type=int, default=None)
    parser.add_argument("--force-shards", action="store_true")
    args = parser.parse_args()

    summary = run_elite_pipeline(
        analysis_root=args.analysis_root,
        leaderboard_path=args.leaderboard,
        raw_root=args.raw_root,
        work_root=args.work_root,
        node_script=args.node_script,
        aliases_path=args.aliases if args.aliases.exists() else None,
        top_fraction_per_role=max(0.01, min(1.0, args.top_fraction_per_role)),
        min_players_per_role=max(1, args.min_players_per_role),
        min_matches=max(1, args.min_matches),
        min_minutes=max(0.0, args.min_minutes),
        max_uncertainty=max(0.0, args.max_uncertainty),
        shard_workers=max(1, args.shard_workers),
        sample_every_ticks=max(1, args.sample_every_ticks),
        window=max(2, args.window),
        sequence_stride=max(1, args.sequence_stride),
        hidden_dim=max(16, args.hidden_dim),
        hidden_dim_2=max(16, args.hidden_dim_2),
        epochs=max(1, args.epochs),
        batch_size=max(32, args.batch_size),
        learning_rate=max(1e-6, args.learning_rate),
        l2=max(0.0, args.l2),
        seed=args.seed,
        replay_limit=args.replay_limit,
        train_replay_limit=args.train_replay_limit,
        validation_replay_limit=args.validation_replay_limit,
        holdout_replay_limit=args.holdout_replay_limit,
        force_shards=args.force_shards,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
