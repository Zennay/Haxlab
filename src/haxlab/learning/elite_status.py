from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def elite_status(
    work_root: Path,
    champions_root: Path | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": "haxlab-elite-status-v1",
        "work_root": str(work_root),
    }

    progress_path = work_root / "progress.json"
    summary_path = work_root / "pipeline-summary.json"
    metrics_path = work_root / "model" / "metrics.json"
    sandbox_path = work_root / "sandbox-benchmark.json"

    if progress_path.exists():
        result["progress"] = json.loads(
            progress_path.read_text(encoding="utf-8")
        )
    else:
        result["progress"] = {"phase": "not_started"}

    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        result["pipeline"] = {
            "best_epoch": summary.get("best_epoch"),
            "kick_threshold": summary.get("kick_threshold"),
            "splits": summary.get("splits"),
            "live_test_gate": summary.get("live_test_gate"),
            "frozen_holdout": summary.get("frozen_holdout"),
        }

    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        result["model"] = {
            "schema": metrics.get("schema"),
            "created_at": metrics.get("created_at"),
            "architecture": metrics.get("architecture"),
            "training": {
                "best_epoch": (metrics.get("training") or {}).get("best_epoch"),
                "kick_threshold_source": (
                    metrics.get("training") or {}
                ).get("kick_threshold_source"),
                "calibrated_kick_thresholds_by_role": (
                    metrics.get("training") or {}
                ).get("calibrated_kick_thresholds_by_role"),
            },
            "final_holdout": metrics.get("final_holdout"),
        }

    if sandbox_path.exists():
        sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
        result["sandbox"] = {
            "matches": sandbox.get("matches"),
            "wins": sandbox.get("wins"),
            "draws": sandbox.get("draws"),
            "losses": sandbox.get("losses"),
            "goals": sandbox.get("goals"),
            "territory": sandbox.get("territory"),
            "runtime_errors": sandbox.get("runtime_errors"),
            "by_elite_side": sandbox.get("by_elite_side"),
        }

    if champions_root is not None:
        registry_path = champions_root / "registry.json"
        if registry_path.exists():
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            result["champion"] = registry.get("current")
            result["champion_history_count"] = len(
                registry.get("history") or []
            )
        else:
            result["champion"] = None
            result["champion_history_count"] = 0

    return result


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-status")
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/elite-player-candidate-b"
        ),
    )
    parser.add_argument(
        "--champions-root",
        type=Path,
        default=Path(
            "/var/lib/haxlab/derived/training/elite-champions"
        ),
    )
    args = parser.parse_args()

    print(
        json.dumps(
            elite_status(args.work_root, args.champions_root),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())