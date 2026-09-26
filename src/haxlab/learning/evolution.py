from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from haxlab.evaluation.champion_registry import (
    load_registry,
    promote_candidate,
)


def run_evolution_cycle(
    *,
    registry_path: Path,
    champions_root: Path,
    candidate_id: str,
    candidate_model_dir: Path,
    metrics_path: Path,
    sandbox_path: Path,
    stadium_path: Path,
    duel_script: Path,
    node_binary: str = "node",
    duel_matches: int = 12,
    duel_minutes: float = 2.0,
    duel_seed: int = 1337,
    sample_every_ticks: int = 6,
    source_ref: str | None = None,
    duel_output_path: Path | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    current = registry.get("current")
    duel_path: Path | None = None
    duel_command: list[str] | None = None

    if current is not None:
        champion_model_dir = Path(str(current["model_dir"]))
        challenger_runtime = candidate_model_dir / "runtime-model.json"
        champion_runtime = champion_model_dir / "runtime-model.json"
        if not challenger_runtime.exists():
            raise FileNotFoundError(
                f"Challenger runtime model missing: {challenger_runtime}"
            )
        if not champion_runtime.exists():
            raise FileNotFoundError(
                f"Champion runtime model missing: {champion_runtime}"
            )
        if not stadium_path.exists():
            raise FileNotFoundError(f"Stadium missing: {stadium_path}")

        duel_path = duel_output_path or (
            candidate_model_dir.parent / "challenger-vs-champion.json"
        )
        duel_path.parent.mkdir(parents=True, exist_ok=True)
        duel_command = [
            node_binary,
            str(duel_script),
            "--challenger",
            str(challenger_runtime),
            "--champion",
            str(champion_runtime),
            "--stadium",
            str(stadium_path),
            "--matches",
            str(max(2, int(duel_matches))),
            "--minutes",
            str(max(0.25, float(duel_minutes))),
            "--seed",
            str(int(duel_seed)),
            "--sample-every",
            str(max(1, int(sample_every_ticks))),
            "--output",
            str(duel_path),
        ]
        completed = subprocess.run(
            duel_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3600,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "Champion duel failed with exit "
                f"{completed.returncode}: {completed.stderr[-4000:]}"
            )
        if not duel_path.exists():
            raise RuntimeError(
                "Champion duel completed without producing its output file"
            )

    promotion = promote_candidate(
        registry_path=registry_path,
        champions_root=champions_root,
        candidate_id=candidate_id,
        candidate_model_dir=candidate_model_dir,
        metrics_path=metrics_path,
        sandbox_path=sandbox_path,
        duel_path=duel_path,
        source_ref=source_ref,
    )

    return {
        "schema": "haxlab-elite-evolution-cycle-v1",
        "candidate_id": candidate_id,
        "had_existing_champion": current is not None,
        "previous_champion": current,
        "duel_path": str(duel_path) if duel_path else None,
        "duel_command": duel_command,
        "promotion": promotion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-evolve")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--champions-root", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-model-dir", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--stadium", type=Path, required=True)
    parser.add_argument("--duel-script", type=Path, required=True)
    parser.add_argument("--node-binary", default="node")
    parser.add_argument("--duel-matches", type=int, default=12)
    parser.add_argument("--duel-minutes", type=float, default=2.0)
    parser.add_argument("--duel-seed", type=int, default=1337)
    parser.add_argument("--sample-every-ticks", type=int, default=6)
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--duel-output", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = run_evolution_cycle(
        registry_path=args.registry,
        champions_root=args.champions_root,
        candidate_id=args.candidate_id,
        candidate_model_dir=args.candidate_model_dir,
        metrics_path=args.metrics,
        sandbox_path=args.sandbox,
        stadium_path=args.stadium,
        duel_script=args.duel_script,
        node_binary=args.node_binary,
        duel_matches=args.duel_matches,
        duel_minutes=args.duel_minutes,
        duel_seed=args.duel_seed,
        sample_every_ticks=args.sample_every_ticks,
        source_ref=args.source_ref,
        duel_output_path=args.duel_output,
    )

    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["promotion"]["promoted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())