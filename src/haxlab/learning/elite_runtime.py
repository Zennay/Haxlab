from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import numpy as np

from haxlab.learning.elite import _forward, direction_from_class


ROLE_IDS = {"gk": 0, "dm": 1, "am": 2, "st": 3}


class ElitePolicy:
    def __init__(self, model_dir: Path) -> None:
        self.model_dir = Path(model_dir)
        self.metadata = json.loads(
            (self.model_dir / "metrics.json").read_text(encoding="utf-8")
        )
        arrays = np.load(self.model_dir / "model.npz")
        self.mean = arrays["mean"].astype(np.float32)
        self.std = arrays["std"].astype(np.float32)
        self.params = {
            key: arrays[key].astype(np.float32)
            for key in ("w1", "b1", "w2", "b2", "wd", "bd", "wk", "bk")
        }
        self.input_columns = list(self.metadata["base_input_columns"])
        self.window = int(self.metadata["architecture"]["window"])
        self.kick_threshold = float(
            self.metadata["training"]["calibrated_kick_threshold"]
        )
        self.kick_max_distance = float(
            (self.metadata.get("runtime") or {}).get("kick_max_distance", 31.0)
        )
        self.history: dict[str, deque[np.ndarray]] = defaultdict(
            lambda: deque(maxlen=self.window)
        )

    def reset(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self.history.clear()
        else:
            self.history.pop(str(agent_id), None)

    def _role_id(self, role: str | int) -> int:
        if isinstance(role, int):
            role_id = role
        else:
            key = str(role).strip().lower()
            if key not in ROLE_IDS:
                raise ValueError(f"unknown role {role!r}; expected GK/DM/AM/ST")
            role_id = ROLE_IDS[key]
        if role_id not in (0, 1, 2, 3):
            raise ValueError(f"invalid role id: {role_id}")
        return role_id

    def vectorize(self, features: dict[str, Any]) -> np.ndarray:
        missing = [name for name in self.input_columns if name not in features]
        if missing:
            raise ValueError(
                "state is missing model features: " + ", ".join(missing[:12])
            )
        return np.asarray(
            [float(features[name]) for name in self.input_columns],
            dtype=np.float32,
        )

    def act(
        self,
        *,
        agent_id: str,
        role: str | int,
        features: dict[str, Any],
    ) -> dict[str, Any]:
        role_id = self._role_id(role)
        raw = self.vectorize(features)
        history = self.history[str(agent_id)]
        history.append(raw)

        values = list(history)
        if not values:
            values = [raw]
        if len(values) < self.window:
            values = [values[0]] * (self.window - len(values)) + values

        sequence = np.stack(values[-self.window :], axis=0)
        normalized = ((sequence - self.mean) / self.std).astype(np.float32)
        flat = normalized.reshape(1, -1)
        role_one_hot = np.zeros((1, 4), dtype=np.float32)
        role_one_hot[0, role_id] = 1.0
        model_x = np.concatenate([flat, role_one_hot], axis=1)

        _, _, dir_prob, kick_prob = _forward(model_x, self.params)
        direction_class = int(dir_prob[0].argmax())
        dir_x, dir_y = direction_from_class(direction_class)
        kick_probability = float(kick_prob[0])
        ball_distance = math.hypot(
            float(features.get("ball_dx", 0.0)),
            float(features.get("ball_dy", 0.0)),
        )
        kick_in_range = ball_distance <= self.kick_max_distance
        kick_requested = kick_probability >= self.kick_threshold
        kick = kick_requested and kick_in_range

        return {
            "agent_id": str(agent_id),
            "role": str(role).lower(),
            "role_id": role_id,
            "dir_x": int(dir_x),
            "dir_y": int(dir_y),
            "kick": bool(kick),
            "kick_probability": kick_probability,
            "kick_threshold": self.kick_threshold,
            "kick_requested": bool(kick_requested),
            "kick_in_range": bool(kick_in_range),
            "kick_max_distance": self.kick_max_distance,
            "ball_distance": ball_distance,
            "direction_class": direction_class,
            "direction_probability": float(dir_prob[0, direction_class]),
            "history_frames": len(history),
            "window": self.window,
        }


def _serve_stdio(policy: ElitePolicy) -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            command = str(request.get("command") or "act")
            request_id = request.get("request_id")
            if command == "reset":
                agent_id = request.get("agent_id")
                policy.reset(None if agent_id is None else str(agent_id))
                response = {
                    "ok": True,
                    "command": "reset",
                    "agent_id": agent_id,
                    "request_id": request_id,
                }
            elif command == "info":
                response = {
                    "ok": True,
                    "request_id": request_id,
                    "schema": policy.metadata.get("schema"),
                    "window": policy.window,
                    "kick_threshold": policy.kick_threshold,
                    "kick_max_distance": policy.kick_max_distance,
                    "input_columns": policy.input_columns,
                }
            else:
                response = {
                    "ok": True,
                    "request_id": request_id,
                    **policy.act(
                        agent_id=str(request.get("agent_id") or "default"),
                        role=request["role"],
                        features=dict(request["features"]),
                    ),
                }
        except Exception as exc:
            response = {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc),
            }
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-agent")
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Read one JSON request per line from stdin and emit one action per line.",
    )
    parser.add_argument("--role", choices=["gk", "dm", "am", "st"], default=None)
    parser.add_argument("--state", type=Path, default=None)
    parser.add_argument("--agent-id", default="default")
    args = parser.parse_args()

    policy = ElitePolicy(args.model_dir)
    if args.stdio:
        return _serve_stdio(policy)

    if args.state is None or args.role is None:
        raise SystemExit("use --stdio or provide both --state and --role")

    payload = json.loads(args.state.read_text(encoding="utf-8"))
    states = payload if isinstance(payload, list) else [payload]
    result: dict[str, Any] | None = None
    for state in states:
        result = policy.act(
            agent_id=args.agent_id,
            role=args.role,
            features=dict(state),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
