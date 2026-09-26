from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

from haxlab.learning.elite import _load_index, evaluate


def evaluate_model(
    *,
    model_dir: Path,
    index_path: Path,
    future_horizon_steps: int = 5,
) -> dict[str, Any]:
    metadata = json.loads(
        (model_dir / "metrics.json").read_text(encoding="utf-8")
    )
    arrays = np.load(model_dir / "model.npz")

    params = {
        key: arrays[key].astype(np.float32)
        for key in ("w1", "b1", "w2", "b2", "wd", "bd", "wk", "bk")
    }
    future_head_available = "wf" in arrays.files and "bf" in arrays.files
    if future_head_available:
        params["wf"] = arrays["wf"].astype(np.float32)
        params["bf"] = arrays["bf"].astype(np.float32)
    else:
        # The old champion can still be evaluated on exactly the same temporal
        # subset as a future-head challenger. Future metrics for this legacy
        # fallback are informational only; immediate movement/kick metrics are
        # fully comparable.
        params["wf"] = params["wd"].copy()
        params["bf"] = params["bd"].copy()

    mean = arrays["mean"].astype(np.float32)
    std = arrays["std"].astype(np.float32)
    role_weights = arrays["role_weights"].astype(np.float32)
    index = _load_index(index_path)

    architecture = metadata.get("architecture") or {}
    training = metadata.get("training") or {}
    metrics, _, _ = evaluate(
        index,
        params=params,
        mean=mean,
        std=std,
        role_weights=role_weights,
        window=int(architecture.get("window") or 8),
        sequence_stride=int(architecture.get("sequence_stride") or 1),
        batch_size=int(training.get("batch_size") or 2048),
        kick_threshold=float(
            training.get("calibrated_kick_threshold", 0.5)
        ),
        future_horizon_steps=max(1, int(future_horizon_steps)),
    )

    return {
        "schema": "haxlab-elite-model-eval-v1",
        "model_dir": str(model_dir),
        "index_path": str(index_path),
        "future_head_available": future_head_available,
        "future_horizon_steps": max(1, int(future_horizon_steps)),
        "final_holdout": metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-eval")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--future-horizon-steps", type=int, default=5)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    result = evaluate_model(
        model_dir=args.model_dir,
        index_path=args.index,
        future_horizon_steps=max(1, args.future_horizon_steps),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result["final_holdout"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
