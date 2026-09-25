from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from haxlab.learning.baseline import direction_from_class


@dataclass(frozen=True)
class ActionPrediction:
    dir_x: int
    dir_y: int
    kick: bool
    direction_class: int
    direction_confidence: float
    kick_probability: float


class NumpyBCPolicy:
    """Inference wrapper for HaxLab's baseline NumPy multitask MLP."""

    def __init__(self, model_dir: Path):
        self.model_dir = Path(model_dir)
        metrics_path = self.model_dir / "metrics.json"
        model_path = self.model_dir / "model.npz"

        metadata = json.loads(metrics_path.read_text(encoding="utf-8"))
        if metadata.get("schema") != "haxlab-bc-baseline-v1":
            raise ValueError(
                f"unsupported model schema: {metadata.get('schema')!r}"
            )

        self.metadata = metadata
        self.input_columns = tuple(metadata.get("input_columns") or ())
        if not self.input_columns:
            raise ValueError("model metadata has no input_columns")

        with np.load(model_path) as archive:
            self.mean = archive["mean"].astype(np.float32)
            self.std = archive["std"].astype(np.float32)
            self.w1 = archive["w1"].astype(np.float32)
            self.b1 = archive["b1"].astype(np.float32)
            self.wd = archive["wd"].astype(np.float32)
            self.bd = archive["bd"].astype(np.float32)
            self.wk = archive["wk"].astype(np.float32)
            self.bk = archive["bk"].astype(np.float32)

        input_dim = len(self.input_columns)
        if self.mean.shape != (input_dim,) or self.std.shape != (input_dim,):
            raise ValueError("normalization shape does not match input columns")
        if self.w1.shape[0] != input_dim:
            raise ValueError("w1 input dimension does not match input columns")
        if self.wd.shape[1] != 9:
            raise ValueError("direction head must have 9 classes")

    @staticmethod
    def _softmax(logits: np.ndarray) -> np.ndarray:
        shifted = logits - np.max(logits)
        exp = np.exp(shifted)
        return exp / np.sum(exp)

    @staticmethod
    def _sigmoid(value: float) -> float:
        value = float(np.clip(value, -30.0, 30.0))
        return float(1.0 / (1.0 + np.exp(-value)))

    def vectorize(self, features: Mapping[str, float]) -> np.ndarray:
        missing = [name for name in self.input_columns if name not in features]
        if missing:
            raise ValueError(f"missing model features: {missing}")
        values = np.asarray(
            [float(features[name]) for name in self.input_columns],
            dtype=np.float32,
        )
        if not np.isfinite(values).all():
            raise ValueError("features must all be finite")
        return values

    def predict(
        self,
        features: Mapping[str, float],
        *,
        kick_threshold: float = 0.5,
    ) -> ActionPrediction:
        x = self.vectorize(features)
        normalized = (x - self.mean) / self.std
        hidden = np.maximum(normalized @ self.w1 + self.b1, 0.0)

        direction_prob = self._softmax(hidden @ self.wd + self.bd)
        direction_class = int(np.argmax(direction_prob))
        dir_x, dir_y = direction_from_class(direction_class)

        kick_logit = float((hidden @ self.wk + self.bk).reshape(-1)[0])
        kick_probability = self._sigmoid(kick_logit)

        return ActionPrediction(
            dir_x=dir_x,
            dir_y=dir_y,
            kick=kick_probability >= float(kick_threshold),
            direction_class=direction_class,
            direction_confidence=float(direction_prob[direction_class]),
            kick_probability=kick_probability,
        )


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-predict-bc")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--features-json",
        required=True,
        help="JSON object keyed by the model's input column names.",
    )
    parser.add_argument("--kick-threshold", type=float, default=0.5)
    args = parser.parse_args()

    features: dict[str, Any] = json.loads(args.features_json)
    if not isinstance(features, dict):
        raise SystemExit("--features-json must decode to an object")

    policy = NumpyBCPolicy(args.model_dir)
    prediction = policy.predict(
        features,
        kick_threshold=max(0.0, min(1.0, args.kick_threshold)),
    )
    print(json.dumps(asdict(prediction), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
