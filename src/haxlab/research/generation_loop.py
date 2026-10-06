from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "haxlab-generation-loop-v1"
PREREGISTRATION_SCHEMA = "haxlab-generation-preregistration-v1"
LEDGER_SCHEMA = "haxlab-generation-ledger-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.is_file():
        return dict(default or {})
    return json.loads(path.read_text(encoding="utf-8"))


def experiment_for_generation(generation: int) -> dict[str, Any]:
    """Return a bounded, deterministic experiment; no model/code mutation is allowed."""
    variants = (
        {"hidden_dim": 64, "epochs": 4, "learning_rate": 0.0010, "l2": 0.00001, "batch_size": 8192},
        {"hidden_dim": 96, "epochs": 4, "learning_rate": 0.0008, "l2": 0.00001, "batch_size": 8192},
        {"hidden_dim": 128, "epochs": 4, "learning_rate": 0.0006, "l2": 0.00002, "batch_size": 8192},
        {"hidden_dim": 64, "epochs": 6, "learning_rate": 0.0005, "l2": 0.00001, "batch_size": 8192},
        {"hidden_dim": 96, "epochs": 5, "learning_rate": 0.0004, "l2": 0.00005, "batch_size": 4096},
        {"hidden_dim": 128, "epochs": 3, "learning_rate": 0.0010, "l2": 0.00005, "batch_size": 4096},
        {"hidden_dim": 64, "epochs": 8, "learning_rate": 0.0003, "l2": 0.00002, "batch_size": 4096},
        {"hidden_dim": 96, "epochs": 6, "learning_rate": 0.0007, "l2": 0.00002, "batch_size": 8192},
    )
    variant = dict(variants[(generation - 1) % len(variants)])
    variant["seed"] = 1337 + (generation * 7919)
    variant["variant_index"] = (generation - 1) % len(variants)
    return variant


SNAPSHOT_RATE_FIELDS = ("direction_accuracy", "joint_accuracy", "kick_f1")


def _snapshot_issues(snapshot: Any, label: str) -> list[str]:
    if not isinstance(snapshot, dict):
        return [f"invalid_{label}:not_object"]

    issues: list[str] = []
    for field in SNAPSHOT_RATE_FIELDS:
        if field not in snapshot:
            issues.append(f"missing_{label}.{field}")
            continue
        value = snapshot[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            issues.append(f"invalid_{label}.{field}:non_numeric")
            continue
        number = float(value)
        if not math.isfinite(number):
            issues.append(f"invalid_{label}.{field}:non_finite")
        elif number < 0.0:
            issues.append(f"invalid_{label}.{field}:below_minimum")
        elif number > 1.0:
            issues.append(f"invalid_{label}.{field}:above_maximum")

    if "samples" not in snapshot:
        issues.append(f"missing_{label}.samples")
    else:
        samples = snapshot["samples"]
        if isinstance(samples, bool) or not isinstance(samples, (int, float)):
            issues.append(f"invalid_{label}.samples:non_numeric")
        else:
            number = float(samples)
            if not math.isfinite(number):
                issues.append(f"invalid_{label}.samples:non_finite")
            elif not number.is_integer():
                issues.append(f"invalid_{label}.samples:not_integer")
            elif number < 1.0:
                issues.append(f"invalid_{label}.samples:below_minimum")
    return issues


def metric_snapshot(metrics: dict[str, Any]) -> dict[str, float]:
    if not isinstance(metrics, dict):
        raise ValueError("invalid_generation_metrics:not_object")
    holdout = metrics.get("final_holdout")
    issues = _snapshot_issues(holdout, "final_holdout")
    if issues:
        raise ValueError("invalid generation metric evidence: " + ", ".join(issues))
    assert isinstance(holdout, dict)
    return {
        "direction_accuracy": float(holdout["direction_accuracy"]),
        "joint_accuracy": float(holdout["joint_accuracy"]),
        "kick_f1": float(holdout["kick_f1"]),
        "samples": float(holdout["samples"]),
    }


def composite_score(snapshot: dict[str, float]) -> float:
    # Joint action accuracy is primary; kick F1 prevents movement-only regressions.
    return 0.70 * snapshot["joint_accuracy"] + 0.30 * snapshot["kick_f1"]


def generation_state_issues(
    state: Any,
    *,
    analysis_version: str,
    manifest_sha256: str,
) -> list[str]:
    if not isinstance(state, dict):
        return ["invalid_generation_state:not_object"]

    issues: list[str] = []
    if state.get("schema") != SCHEMA:
        issues.append("generation_state.schema_mismatch")
    if state.get("analysis_version") != analysis_version:
        issues.append("generation_state.analysis_version_mismatch")

    stored_manifest_sha256 = state.get("manifest_sha256")
    if not isinstance(stored_manifest_sha256, str):
        issues.append("generation_state.manifest_sha256_invalid")
    elif stored_manifest_sha256 != manifest_sha256:
        issues.append("generation_state.manifest_sha256_mismatch")

    champion = state.get("champion")
    if not isinstance(champion, dict):
        issues.append("generation_state.champion_invalid")
        return issues

    champion_id = champion.get("id")
    if not isinstance(champion_id, str) or not champion_id.strip():
        issues.append("generation_state.champion_id_invalid")

    champion_metrics = champion.get("metrics")
    metric_issues = _snapshot_issues(
        champion_metrics,
        "generation_state.champion.metrics",
    )
    issues.extend(metric_issues)

    score = champion.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        issues.append("generation_state.champion_score_invalid")
    elif not math.isfinite(float(score)):
        issues.append("generation_state.champion_score_non_finite")
    elif not metric_issues and isinstance(champion_metrics, dict):
        expected_score = composite_score(champion_metrics)
        if not math.isclose(
            float(score),
            expected_score,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            issues.append("generation_state.champion_score_mismatch")

    return issues


def preregistration_issues(
    preregistration: Any,
    *,
    state: dict[str, Any],
    generation: int,
    analysis_version: str,
    manifest_sha256: str,
    shard_root: Path,
) -> list[str]:
    if not isinstance(preregistration, dict):
        return ["invalid_preregistration:not_object"]

    issues: list[str] = []
    expected_experiment_id = f"gen-{generation:04d}"
    expected_parent = state.get("champion", {}).get("id")
    expected_hyperparameters = experiment_for_generation(generation)

    if preregistration.get("schema") != PREREGISTRATION_SCHEMA:
        issues.append("preregistration.schema_mismatch")
    if type(preregistration.get("generation")) is not int:
        issues.append("preregistration.generation_invalid")
    elif preregistration["generation"] != generation:
        issues.append("preregistration.generation_mismatch")
    if preregistration.get("experiment_id") != expected_experiment_id:
        issues.append("preregistration.experiment_id_mismatch")
    if preregistration.get("parent_champion") != expected_parent:
        issues.append("preregistration.parent_champion_mismatch")
    if preregistration.get("analysis_version") != analysis_version:
        issues.append("preregistration.analysis_version_mismatch")
    if preregistration.get("dataset_manifest_sha256") != manifest_sha256:
        issues.append("preregistration.manifest_sha256_mismatch")

    split = preregistration.get("split")
    if not isinstance(split, dict):
        issues.append("preregistration.split_invalid")
    else:
        if split.get("train_index") != str(shard_root / "train" / "_index.json"):
            issues.append("preregistration.train_index_mismatch")
        if split.get("holdout_index") != str(shard_root / "holdout" / "_index.json"):
            issues.append("preregistration.holdout_index_mismatch")
        if split.get("holdout_is_frozen") is not True:
            issues.append("preregistration.holdout_not_frozen")

    budget = preregistration.get("budget")
    if not isinstance(budget, dict):
        issues.append("preregistration.budget_invalid")
    else:
        if budget.get("paper_or_offline_only") is not True:
            issues.append("preregistration.paper_or_offline_only_required")
        if budget.get("no_external_ai_calls") is not True:
            issues.append("preregistration.no_external_ai_calls_required")
        if budget.get("max_epochs") != 8:
            issues.append("preregistration.max_epochs_mismatch")
        if budget.get("max_hidden_dim") != 128:
            issues.append("preregistration.max_hidden_dim_mismatch")

    if preregistration.get("hyperparameters") != expected_hyperparameters:
        issues.append("preregistration.hyperparameters_mismatch")
    if preregistration.get("seed") != expected_hyperparameters["seed"]:
        issues.append("preregistration.seed_mismatch")

    stored_sha256 = preregistration.get("preregistration_sha256")
    if not isinstance(stored_sha256, str):
        issues.append("preregistration.sha256_invalid")
    else:
        unhashed = dict(preregistration)
        unhashed.pop("preregistration_sha256", None)
        expected_sha256 = hashlib.sha256(canonical_json(unhashed)).hexdigest()
        if stored_sha256 != expected_sha256:
            issues.append("preregistration.sha256_mismatch")

    return issues


def evaluate_candidate(
    candidate: dict[str, float],
    champion: dict[str, float],
    *,
    minimum_improvement: float = 0.001,
    maximum_direction_regression: float = 0.02,
    maximum_kick_regression: float = 0.05,
) -> dict[str, Any]:
    evidence_issues = [
        *_snapshot_issues(candidate, "candidate"),
        *_snapshot_issues(champion, "champion"),
    ]
    for label, value in (
        ("minimum_improvement", minimum_improvement),
        ("maximum_direction_regression", maximum_direction_regression),
        ("maximum_kick_regression", maximum_kick_regression),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            evidence_issues.append(f"invalid_policy.{label}:non_numeric")
            continue
        number = float(value)
        if not math.isfinite(number):
            evidence_issues.append(f"invalid_policy.{label}:non_finite")
        elif number < 0.0:
            evidence_issues.append(f"invalid_policy.{label}:below_minimum")
        elif number > 1.0:
            evidence_issues.append(f"invalid_policy.{label}:above_maximum")

    if evidence_issues:
        return {
            "promote": False,
            "candidate_score": None,
            "champion_score": None,
            "improvement": None,
            "reasons": evidence_issues,
        }

    candidate_score = composite_score(candidate)
    champion_score = composite_score(champion)
    regressions: list[str] = []
    if candidate["direction_accuracy"] < champion["direction_accuracy"] - maximum_direction_regression:
        regressions.append("direction_accuracy_regression")
    if candidate["kick_f1"] < champion["kick_f1"] - maximum_kick_regression:
        regressions.append("kick_f1_regression")
    improved = candidate_score >= champion_score + minimum_improvement
    promoted = improved and not regressions
    reasons = ["offline_holdout_gate_passed"] if promoted else []
    if not improved:
        reasons.append(f"insufficient_improvement:{candidate_score:.6f}<{champion_score + minimum_improvement:.6f}")
    reasons.extend(regressions)
    return {
        "promote": promoted,
        "candidate_score": candidate_score,
        "champion_score": champion_score,
        "improvement": candidate_score - champion_score,
        "reasons": reasons,
    }


def mine_failure(
    generation: int,
    candidate: dict[str, float],
    champion: dict[str, float],
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "haxlab-failure-inventory-v1",
        "generation": generation,
        "created_at": utc_now(),
        "candidate_metrics": candidate,
        "champion_metrics": champion,
        "candidate_score": decision["candidate_score"],
        "champion_score": decision["champion_score"],
        "improvement": decision["improvement"],
        "reasons": decision["reasons"],
        "next_action": "continue_with_next_bounded_experiment",
    }


class GenerationLoop:
    def __init__(
        self,
        *,
        app_dir: Path,
        state_dir: Path,
        models_dir: Path,
        derived_dir: Path,
        analysis_version: str,
        manifest: Path,
        shard_root: Path,
        max_generations_per_tick: int = 1,
    ) -> None:
        self.app_dir = app_dir
        self.state_dir = state_dir
        self.models_dir = models_dir
        self.derived_dir = derived_dir
        self.analysis_version = analysis_version
        self.manifest = manifest
        self.shard_root = shard_root
        self.max_generations_per_tick = max(1, max_generations_per_tick)
        self.generations_dir = models_dir / "generations"
        self.champions_dir = models_dir / "champions"
        self.state_path = state_dir / "generation-state.json"
        self.status_path = state_dir / "autonomy-status.json"
        self.ledger_path = state_dir / "generation-ledger.jsonl"

    def status(self, state: str, action: str, detail: str, **extra: Any) -> None:
        payload = {
            "schema": "haxlab-autonomy-status-v2",
            "state": state,
            "action": action,
            "detail": detail,
            "updated_at": utc_now(),
            **extra,
        }
        atomic_json(self.status_path, payload)

    def load_state(self) -> dict[str, Any]:
        return load_json(self.state_path, {"schema": SCHEMA, "next_generation": 1})

    def save_state(self, state: dict[str, Any]) -> None:
        state["updated_at"] = utc_now()
        atomic_json(self.state_path, state)

    def initialize(self) -> dict[str, Any]:
        state = self.load_state()
        if not isinstance(state, dict):
            raise RuntimeError("generation state provenance validation failed: invalid_generation_state:not_object")
        if state.get("champion"):
            if not self.manifest.is_file():
                raise RuntimeError(f"dataset manifest is missing: {self.manifest}")
            issues = generation_state_issues(
                state,
                analysis_version=self.analysis_version,
                manifest_sha256=sha256_file(self.manifest),
            )
            if issues:
                raise RuntimeError(
                    "generation state provenance validation failed: "
                    + ", ".join(issues)
                )
            return state

        baseline_dir = self.models_dir / "challengers" / "autonomy-bc-baseline-v1"
        baseline_metrics_path = baseline_dir / "metrics.json"
        if not baseline_metrics_path.is_file():
            raise RuntimeError(f"bootstrap baseline is missing: {baseline_metrics_path}")

        metrics = load_json(baseline_metrics_path)
        snapshot = metric_snapshot(metrics)
        champion_id = "gen-0000-bootstrap"
        champion_pointer = {
            "id": champion_id,
            "generation": 0,
            "model_dir": str(baseline_dir),
            "metrics_path": str(baseline_metrics_path),
            "metrics": snapshot,
            "score": composite_score(snapshot),
            "scope": "offline_behavior_only",
        }
        self.champions_dir.mkdir(parents=True, exist_ok=True)
        atomic_json(self.champions_dir / "champion.json", champion_pointer)
        state.update(
            {
                "schema": SCHEMA,
                "analysis_version": self.analysis_version,
                "manifest": str(self.manifest),
                "manifest_sha256": sha256_file(self.manifest),
                "shard_root": str(self.shard_root),
                "next_generation": 1,
                "champion": champion_pointer,
                "active": None,
                "last_result": {
                    "generation": 0,
                    "status": "bootstrap_champion",
                    "created_at": utc_now(),
                },
            }
        )
        self.save_state(state)
        return state

    def preregister(self, state: dict[str, Any], generation: int) -> dict[str, Any]:
        experiment = experiment_for_generation(generation)
        candidate_dir = self.generations_dir / f"gen-{generation:04d}"
        prereg_path = candidate_dir / "preregistration.json"
        manifest_sha256 = sha256_file(self.manifest)
        if prereg_path.is_file():
            cached = load_json(prereg_path)
            issues = preregistration_issues(
                cached,
                state=state,
                generation=generation,
                analysis_version=self.analysis_version,
                manifest_sha256=manifest_sha256,
                shard_root=self.shard_root,
            )
            if issues:
                raise RuntimeError(
                    "generation preregistration validation failed: "
                    + ", ".join(issues)
                )
            return cached

        revision = "unknown"
        try:
            revision = subprocess.check_output(
                ["git", "-C", str(self.app_dir), "rev-parse", "HEAD"],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            pass

        payload = {
            "schema": PREREGISTRATION_SCHEMA,
            "experiment_id": f"gen-{generation:04d}",
            "generation": generation,
            "parent_champion": state["champion"]["id"],
            "analysis_version": self.analysis_version,
            "dataset_manifest": str(self.manifest),
            "dataset_manifest_sha256": manifest_sha256,
            "split": {
                "train_index": str(self.shard_root / "train" / "_index.json"),
                "holdout_index": str(self.shard_root / "holdout" / "_index.json"),
                "holdout_is_frozen": True,
            },
            "budget": {
                "max_generations_per_tick": self.max_generations_per_tick,
                "max_epochs": 8,
                "max_hidden_dim": 128,
                "paper_or_offline_only": True,
                "no_external_ai_calls": True,
            },
            "hyperparameters": experiment,
            "seed": experiment["seed"],
            "code_revision": revision,
            "created_at": utc_now(),
        }
        payload["preregistration_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
        issues = preregistration_issues(
            payload,
            state=state,
            generation=generation,
            analysis_version=self.analysis_version,
            manifest_sha256=manifest_sha256,
            shard_root=self.shard_root,
        )
        if issues:
            raise RuntimeError(
                "generated preregistration failed validation: "
                + ", ".join(issues)
            )
        atomic_json(prereg_path, payload)
        return payload

    def run_training(self, prereg: dict[str, Any], output_dir: Path) -> None:
        hp = prereg["hyperparameters"]
        train_index = self.shard_root / "train" / "_index.json"
        holdout_index = self.shard_root / "holdout" / "_index.json"
        if not train_index.is_file() or not holdout_index.is_file():
            raise RuntimeError("immutable training shards are not available")
        command = [
            str(self.app_dir / ".venv" / "bin" / "haxlab-train-bc"),
            "--train-index", str(train_index),
            "--holdout-index", str(holdout_index),
            "--output-dir", str(output_dir),
            "--hidden-dim", str(hp["hidden_dim"]),
            "--epochs", str(hp["epochs"]),
            "--batch-size", str(hp["batch_size"]),
            "--learning-rate", str(hp["learning_rate"]),
            "--l2", str(hp["l2"]),
            "--seed", str(hp["seed"]),
        ]
        subprocess.run(command, check=True, cwd=self.app_dir)

    def append_ledger(self, record: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema": LEDGER_SCHEMA, **record}, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def execute_one(self, state: dict[str, Any]) -> dict[str, Any]:
        generation = int(state["active"]["generation"]) if state.get("active") else int(state["next_generation"])
        prereg = self.preregister(state, generation)
        candidate_dir = self.generations_dir / f"gen-{generation:04d}"
        metrics_path = candidate_dir / "metrics.json"
        state["active"] = {
            "generation": generation,
            "experiment_id": prereg["experiment_id"],
            "phase": "training",
            "preregistration_sha256": prereg["preregistration_sha256"],
            "candidate_dir": str(candidate_dir),
        }
        self.save_state(state)
        self.status(
            "RUNNING",
            "train_generation",
            f"Training {prereg['experiment_id']} from {state['champion']['id']}",
            generation=generation,
            parent_champion=state["champion"]["id"],
        )

        if not metrics_path.is_file():
            self.run_training(prereg, candidate_dir)

        if not metrics_path.is_file():
            raise RuntimeError(f"training completed without metrics: {metrics_path}")

        candidate_metrics = metric_snapshot(load_json(metrics_path))
        champion_metrics = dict(state["champion"]["metrics"])
        decision = evaluate_candidate(candidate_metrics, champion_metrics)
        result = {
            "generation": generation,
            "experiment_id": prereg["experiment_id"],
            "parent_champion": state["champion"]["id"],
            "candidate_metrics": candidate_metrics,
            "decision": decision,
            "metrics_path": str(metrics_path),
            "completed_at": utc_now(),
        }

        if decision["promote"]:
            champion = {
                "id": f"gen-{generation:04d}",
                "generation": generation,
                "model_dir": str(candidate_dir),
                "metrics_path": str(metrics_path),
                "metrics": candidate_metrics,
                "score": decision["candidate_score"],
                "scope": "offline_behavior_only",
                "preregistration_sha256": prereg["preregistration_sha256"],
            }
            self.champions_dir.mkdir(parents=True, exist_ok=True)
            atomic_json(self.champions_dir / f"champion-gen-{generation:04d}.json", champion)
            atomic_json(self.champions_dir / "champion.json", champion)
            state["champion"] = champion
            result["status"] = "promoted_offline"
            self.status(
                "RUNNING",
                "generation_promoted",
                f"{prereg['experiment_id']} promoted as offline champion; continuing to next generation",
                generation=generation,
                champion=champion["id"],
            )
        else:
            failure = mine_failure(generation, candidate_metrics, champion_metrics, decision)
            atomic_json(candidate_dir / "failure-inventory.json", failure)
            result["status"] = "rejected_offline"
            self.status(
                "RUNNING",
                "generation_rejected",
                f"{prereg['experiment_id']} rejected; failure inventory stored; continuing to next generation",
                generation=generation,
                champion=state["champion"]["id"],
            )

        self.append_ledger(result)
        state["last_result"] = result
        state["next_generation"] = generation + 1
        state["active"] = None
        self.save_state(state)
        return result

    def run(self) -> dict[str, Any]:
        state = self.initialize()
        if not self.manifest.is_file():
            raise RuntimeError(f"dataset manifest is missing: {self.manifest}")
        if not self.shard_root.is_dir():
            raise RuntimeError(f"shard root is missing: {self.shard_root}")

        results: list[dict[str, Any]] = []
        for _ in range(self.max_generations_per_tick):
            try:
                results.append(self.execute_one(state))
            except Exception as exc:
                state["last_error"] = {
                    "generation": state.get("active", {}).get("generation"),
                    "error": str(exc),
                    "updated_at": utc_now(),
                }
                self.save_state(state)
                self.status(
                    "FAILED_RETRYABLE",
                    "generation_failed",
                    str(exc),
                    generation=state.get("active", {}).get("generation"),
                )
                break

        payload = {
            "schema": SCHEMA,
            "next_generation": state.get("next_generation"),
            "champion": state.get("champion"),
            "results": results,
            "state_path": str(self.state_path),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-generation-loop")
    parser.add_argument("--app-dir", type=Path, default=Path("/opt/haxlab"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/haxlab/state"))
    parser.add_argument("--models-dir", type=Path, default=Path("/var/lib/haxlab/models"))
    parser.add_argument("--derived-dir", type=Path, default=Path("/var/lib/haxlab/derived"))
    parser.add_argument("--analysis-version", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shard-root", type=Path, required=True)
    parser.add_argument("--max-generations-per-tick", type=int, default=1)
    args = parser.parse_args()

    loop = GenerationLoop(
        app_dir=args.app_dir,
        state_dir=args.state_dir,
        models_dir=args.models_dir,
        derived_dir=args.derived_dir,
        analysis_version=args.analysis_version,
        manifest=args.manifest,
        shard_root=args.shard_root,
        max_generations_per_tick=args.max_generations_per_tick,
    )
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
