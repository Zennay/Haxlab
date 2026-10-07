"""Live inference helpers for the promoted HaxLab champion."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np


DEFAULT_CHAMPION_ROOT = Path("/var/lib/haxlab/derived/champions/elite-player")
ROLE_KEYS = ("role_order", "role_names", "role_labels", "roles")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_version_dir(root: Path, version: Any) -> Path | None:
    if not isinstance(version, str) or not version:
        return None
    if Path(version).name != version or version in {".", ".."}:
        return None

    registry_root = root.resolve()
    versions_path = root / "versions"
    if versions_path.is_symlink():
        return None
    versions_root = versions_path.resolve()
    if versions_root.parent != registry_root or versions_root.name != "versions":
        return None

    raw_candidate = versions_root / version
    if raw_candidate.is_symlink():
        return None
    candidate = raw_candidate.resolve()
    if candidate.parent != versions_root or candidate.name != version:
        return None

    for filename in ("metrics.json", "model.npz"):
        artifact = candidate / filename
        if artifact.is_symlink() or not artifact.is_file():
            return None
        try:
            if artifact.resolve().parent != candidate:
                return None
        except OSError:
            return None
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _live_validation_evidence_matches(
    root: Path,
    *,
    payload: dict[str, Any],
    version: str,
    source_stage: str,
) -> bool:
    raw_path = payload.get("validation_evidence_path")
    expected_sha = payload.get("validation_evidence_sha256")
    if not isinstance(raw_path, str) or not isinstance(expected_sha, str):
        return False
    try:
        registry_root = root.resolve()
        validations_path = root / "validations"
        if validations_path.is_symlink():
            return False
        validations_root = validations_path.resolve()
        if (
            validations_root.parent != registry_root
            or validations_root.name != "validations"
        ):
            return False

        version_validation_path = validations_root / version
        if (
            version_validation_path.is_symlink()
            or not version_validation_path.is_dir()
        ):
            return False
        version_validation_root = version_validation_path.resolve()
        if (
            version_validation_root.parent != validations_root
            or version_validation_root.name != version
        ):
            return False

        source_stage_path = version_validation_root / source_stage
        if source_stage_path.is_symlink() or not source_stage_path.is_dir():
            return False
        evidence_root = source_stage_path.resolve()
        if (
            evidence_root.parent != version_validation_root
            or evidence_root.name != source_stage
        ):
            return False

        raw_evidence_path = Path(raw_path)
        if not raw_evidence_path.is_absolute() or raw_evidence_path.is_symlink():
            return False
        evidence_path = raw_evidence_path.resolve()
        if evidence_path.parent != evidence_root or not evidence_path.is_file():
            return False
        if _sha256_file(evidence_path) != expected_sha:
            return False
        evidence = _load_json(evidence_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(evidence, dict) or evidence.get("validated") is not True:
        return False
    candidate = evidence.get("candidate")
    if isinstance(candidate, dict):
        evidence_version = candidate.get("version_id")
        if evidence_version not in (None, "") and evidence_version != version:
            return False
    return True


def _promoted_live_version_from_pointer(root: Path) -> str | None:
    live_path = root / "live.json"
    if live_path.is_symlink() or not live_path.is_file():
        return None
    try:
        registry_root = root.resolve()
        resolved_live_path = live_path.resolve()
        if (
            resolved_live_path.parent != registry_root
            or resolved_live_path.name != "live.json"
        ):
            return None
        payload = _load_json(live_path)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != "haxlab-champion-pointer-v1":
        return None
    if payload.get("validation_stage") != "live":
        return None
    source_stage = payload.get("source_validation_stage")
    if source_stage not in {"canary", "live"}:
        return None

    version = payload.get("version_id")
    version_dir = _safe_version_dir(root, version)
    if version_dir is None:
        return None

    required_paths = {
        "model_path": version_dir / "model.npz",
        "metrics_path": version_dir / "metrics.json",
    }
    for key, expected in required_paths.items():
        raw = payload.get(key)
        if not isinstance(raw, str):
            return None
        try:
            raw_path = Path(raw)
            if not raw_path.is_absolute():
                return None
            if raw_path.resolve() != expected.resolve():
                return None
        except OSError:
            return None

    if not _live_validation_evidence_matches(
        root,
        payload=payload,
        version=version,
        source_stage=source_stage,
    ):
        return None

    return version


def resolve_version_dir(root: Path, explicit: str | None = None) -> Path:
    version = explicit
    if version:
        path = _safe_version_dir(root, version)
        if path is not None:
            return path
        raise FileNotFoundError(f"champion version not found or unsafe: {version}")

    pointer = _promoted_live_version_from_pointer(root)
    if pointer:
        path = _safe_version_dir(root, pointer)
        if path is not None:
            return path
        raise FileNotFoundError(f"promoted live pointer is no longer loadable: {pointer}")

    raise FileNotFoundError(
        "no valid live-stage champion pointer found below "
        f"{root}; refusing to select current/canary or unpromoted versions"
    )


def _extract_role_order(payload: Any) -> list[str] | None:
    if isinstance(payload, dict):
        for key in ROLE_KEYS:
            value = payload.get(key)
            if isinstance(value, list) and len(value) == 4 and all(isinstance(x, str) for x in value):
                return [x.casefold() for x in value]
            if isinstance(value, dict) and len(value) == 4:
                pairs: list[tuple[int, str]] = []
                for k, v in value.items():
                    if isinstance(v, int):
                        pairs.append((v, str(k).casefold()))
                    elif isinstance(k, str) and k.isdigit() and isinstance(v, str):
                        pairs.append((int(k), v.casefold()))
                if len(pairs) == 4:
                    return [name for _, name in sorted(pairs)]
        for value in payload.values():
            found = _extract_role_order(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _extract_role_order(value)
            if found:
                return found
    return None


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits)
    exp = np.exp(shifted)
    return exp / np.sum(exp)


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


class LiveChampion:
    def __init__(
        self,
        *,
        root: Path = DEFAULT_CHAMPION_ROOT,
        version: str | None = None,
        role: str | None = None,
    ) -> None:
        self.version_dir = resolve_version_dir(root, version)
        self.version = self.version_dir.name
        self.metrics = _load_json(self.version_dir / "metrics.json")
        self.model_path = self.version_dir / "model.npz"
        with np.load(self.model_path, allow_pickle=False) as model:
            self.params = {key: np.array(model[key], copy=True) for key in model.files}

        required = ("mean", "std", "w1", "b1", "w2", "b2", "wd", "bd", "wk", "bk")
        missing = [key for key in required if key not in self.params]
        if missing:
            raise ValueError(f"champion model missing parameters: {missing}")

        self.mean = self.params["mean"].astype(np.float32)
        self.std = np.where(
            self.params["std"].astype(np.float32) < 1e-4,
            1.0,
            self.params["std"].astype(np.float32),
        )
        self.base_columns = list(self.metrics.get("base_input_columns") or [])
        if len(self.base_columns) != int(self.mean.size):
            raise ValueError(
                f"base input mismatch: columns={len(self.base_columns)} mean={self.mean.size}"
            )

        input_dim = int(self.params["w1"].shape[0])
        role_dim = 4
        remaining = input_dim - role_dim
        if remaining <= 0 or remaining % len(self.base_columns) != 0:
            raise ValueError(
                f"cannot derive temporal window: input_dim={input_dim}, frame_dim={len(self.base_columns)}"
            )
        self.window = remaining // len(self.base_columns)

        runtime_payload: dict[str, Any] = {}
        runtime_path = self.metrics.get("runtime_model_path")
        if runtime_path:
            path = Path(str(runtime_path))
            if path.is_file():
                try:
                    runtime_payload = _load_json(path)
                except Exception:
                    runtime_payload = {}

        role_order = _extract_role_order(runtime_payload)
        if not role_order:
            configured = os.environ.get("HAXLAB_ROLE_ORDER", "gk,dm,am,st")
            role_order = [item.strip().casefold() for item in configured.split(",") if item.strip()]
        if len(role_order) != 4 or len(set(role_order)) != 4:
            raise ValueError(f"invalid role order: {role_order}")
        self.role_order = role_order
        self.role = (role or os.environ.get("HAXLAB_LIVE_ROLE", "st")).casefold()
        if self.role not in self.role_order:
            raise ValueError(f"unknown role {self.role!r}; expected one of {self.role_order}")

        final = self.metrics.get("final_validation") or self.metrics.get("final_holdout") or {}
        training = self.metrics.get("training") or {}
        runtime = self.metrics.get("runtime") or {}
        self.kick_threshold = float(
            final.get("kick_threshold")
            or training.get("calibrated_kick_threshold")
            or 0.5
        )
        self.kick_max_distance = float(runtime.get("kick_max_distance") or 31.0)
        self.history: deque[np.ndarray] = deque(maxlen=self.window)

    def reset(self) -> None:
        self.history.clear()

    def metadata(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "model_path": str(self.model_path),
            "schema": self.metrics.get("schema"),
            "frame_dim": len(self.base_columns),
            "window": self.window,
            "temporal_input_dim": int(self.params["w1"].shape[0]),
            "role_order": self.role_order,
            "role": self.role,
            "kick_threshold": self.kick_threshold,
            "kick_max_distance": self.kick_max_distance,
        }

    def infer(self, features: dict[str, Any]) -> dict[str, Any]:
        frame = np.asarray(
            [float(features.get(column, 0.0) or 0.0) for column in self.base_columns],
            dtype=np.float32,
        )
        normalized = (frame - self.mean) / self.std
        self.history.append(normalized)

        sequence = list(self.history)
        if not sequence:
            sequence = [normalized]
        while len(sequence) < self.window:
            sequence.insert(0, sequence[0])
        sequence = sequence[-self.window:]

        role_one_hot = np.zeros(4, dtype=np.float32)
        role_one_hot[self.role_order.index(self.role)] = 1.0
        x = np.concatenate([np.concatenate(sequence), role_one_hot]).astype(np.float32)

        h1 = np.maximum(x @ self.params["w1"] + self.params["b1"], 0.0)
        h2 = np.maximum(h1 @ self.params["w2"] + self.params["b2"], 0.0)
        direction_prob = _softmax(h2 @ self.params["wd"] + self.params["bd"])
        kick_prob = _sigmoid(float((h2 @ self.params["wk"] + self.params["bk"]).reshape(-1)[0]))

        direction_class = int(np.argmax(direction_prob))
        dir_y = direction_class // 3 - 1
        dir_x = direction_class % 3 - 1

        ball_dx = float(features.get("ball_dx", 0.0) or 0.0)
        ball_dy = float(features.get("ball_dy", 0.0) or 0.0)
        ball_distance = math.hypot(ball_dx, ball_dy)
        kick = kick_prob >= self.kick_threshold and ball_distance <= self.kick_max_distance

        return {
            "version": self.version,
            "dir_x": dir_x,
            "dir_y": dir_y,
            "kick": bool(kick),
            "kick_probability": kick_prob,
            "kick_threshold": self.kick_threshold,
            "ball_distance": ball_distance,
            "direction_confidence": float(direction_prob[direction_class]),
        }


def _serve(champion: LiveChampion) -> int:
    print(json.dumps({"type": "ready", **champion.metadata()}, sort_keys=True), flush=True)
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            request = json.loads(raw)
            if request.get("op") == "reset":
                champion.reset()
                response = {"type": "reset", "ok": True}
            elif request.get("op") == "metadata":
                response = {"type": "metadata", **champion.metadata()}
            else:
                response = {"type": "action", **champion.infer(request.get("features") or {})}
        except Exception as exc:
            response = {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(response, sort_keys=True), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m haxlab.live.inference")
    parser.add_argument("--root", type=Path, default=DEFAULT_CHAMPION_ROOT)
    parser.add_argument("--version", default=None)
    parser.add_argument("--role", default=None)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()

    champion = LiveChampion(root=args.root, version=args.version, role=args.role)
    if args.probe:
        print(json.dumps(champion.metadata(), indent=2, sort_keys=True))
        return 0
    return _serve(champion)


if __name__ == "__main__":
    raise SystemExit(main())
