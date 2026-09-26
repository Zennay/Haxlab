from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUIRED_MODEL_FILES = ("model.npz", "runtime-model.json", "metrics.json")
PROMOTION_KEYS = (
    "promote_future_challenger",
    "promote_recovery",
    "promote_candidate",
    "promote",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _promotion_allowed(evidence: dict[str, Any]) -> tuple[bool, str | None]:
    for key in PROMOTION_KEYS:
        if key in evidence:
            return bool(evidence[key]), key
    return False, None


def _selected_runtime_config(evidence: dict[str, Any]) -> dict[str, Any] | None:
    selected = evidence.get("selected")
    if isinstance(selected, dict):
        for key in (
            "future_assist_config",
            "recovery_config",
            "runtime_config",
        ):
            value = selected.get(key)
            if isinstance(value, dict):
                return value
    return None


def promote_champion(
    *,
    model_dir: Path,
    promotion_evidence_path: Path,
    registry_root: Path,
    candidate_name: str,
    code_commit: str | None = None,
) -> dict[str, Any]:
    model_dir = model_dir.resolve()
    promotion_evidence_path = promotion_evidence_path.resolve()
    registry_root = registry_root.resolve()

    if not promotion_evidence_path.is_file():
        raise FileNotFoundError(promotion_evidence_path)

    evidence = json.loads(promotion_evidence_path.read_text(encoding="utf-8"))
    allowed, promotion_key = _promotion_allowed(evidence)
    if promotion_key is None:
        raise ValueError(
            "promotion evidence has no recognized promotion decision key"
        )
    if not allowed:
        raise ValueError(
            f"promotion evidence explicitly rejects candidate via {promotion_key}"
        )

    files: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_MODEL_FILES:
        path = model_dir / name
        if not path.is_file():
            raise FileNotFoundError(path)
        files[name] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }

    model_sha = files["model.npz"]["sha256"]
    runtime_sha = files["runtime-model.json"]["sha256"]
    runtime_config = _selected_runtime_config(evidence)
    behavior_payload = json.dumps(
        {
            "model_sha256": model_sha,
            "runtime_model_sha256": runtime_sha,
            "runtime_config": runtime_config,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    behavior_sha = hashlib.sha256(behavior_payload).hexdigest()
    version_id = f"{candidate_name}-{behavior_sha[:12]}"

    versions_root = registry_root / "versions"
    destination = versions_root / version_id
    current_path = registry_root / "current.json"

    manifest = {
        "schema": "haxlab-champion-registry-v1",
        "version_id": version_id,
        "candidate_name": candidate_name,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": code_commit,
        "source_model_dir": str(model_dir),
        "promotion_evidence_source": str(promotion_evidence_path),
        "promotion_key": promotion_key,
        "model_sha256": model_sha,
        "runtime_model_sha256": runtime_sha,
        "behavior_sha256": behavior_sha,
        "files": files,
        "runtime_config": runtime_config,
        "promotion_evidence": evidence,
    }

    if destination.exists():
        existing_manifest_path = destination / "manifest.json"
        if not existing_manifest_path.is_file():
            raise FileExistsError(
                f"registry version exists without manifest: {destination}"
            )
        existing = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        if (
            existing.get("model_sha256") != model_sha
            or existing.get("runtime_model_sha256") != runtime_sha
            or existing.get("runtime_config") != runtime_config
        ):
            raise FileExistsError(
                f"registry version collision with different behavior: {version_id}"
            )
        # Idempotent re-promotion of the exact same immutable model.
        pointer = {
            "schema": "haxlab-champion-pointer-v1",
            "version_id": version_id,
            "manifest_path": str(existing_manifest_path),
            "model_path": str(destination / "model.npz"),
            "runtime_model_path": str(destination / "runtime-model.json"),
            "metrics_path": str(destination / "metrics.json"),
            "model_sha256": model_sha,
            "runtime_model_sha256": runtime_sha,
            "behavior_sha256": behavior_sha,
            "runtime_config": runtime_config,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(current_path, pointer)
        return {
            "promoted": True,
            "idempotent": True,
            "version_id": version_id,
            "version_dir": str(destination),
            "current_path": str(current_path),
        }

    versions_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{version_id}.", dir=versions_root)
    )
    try:
        for name in REQUIRED_MODEL_FILES:
            shutil.copy2(model_dir / name, temporary / name)
        shutil.copy2(promotion_evidence_path, temporary / "promotion-evidence.json")
        _atomic_json(temporary / "manifest.json", manifest)
        os.replace(temporary, destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise

    pointer = {
        "schema": "haxlab-champion-pointer-v1",
        "version_id": version_id,
        "manifest_path": str(destination / "manifest.json"),
        "model_path": str(destination / "model.npz"),
        "runtime_model_path": str(destination / "runtime-model.json"),
        "metrics_path": str(destination / "metrics.json"),
        "model_sha256": model_sha,
        "runtime_model_sha256": runtime_sha,
        "behavior_sha256": behavior_sha,
        "runtime_config": manifest["runtime_config"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(current_path, pointer)

    return {
        "promoted": True,
        "idempotent": False,
        "version_id": version_id,
        "version_dir": str(destination),
        "current_path": str(current_path),
        "model_sha256": model_sha,
        "runtime_model_sha256": runtime_sha,
        "behavior_sha256": behavior_sha,
    }


VALIDATION_STAGE_ORDER = {
    "promotion": 0,
    "multi_replay": 10,
    "canary": 20,
    "runtime": 25,
    "live": 30,
}


def _validation_summary(evidence: dict[str, Any]) -> dict[str, Any]:
    aggregate = evidence.get("aggregate")
    if not isinstance(aggregate, dict):
        return {}
    keep = (
        "replay_count",
        "mean_movement_delta",
        "median_movement_delta",
        "worst_movement_delta",
        "movement_nonnegative_replays",
        "mean_progression_delta",
        "worst_progression_delta",
        "mean_territory_delta",
        "worst_territory_delta",
        "mean_near_ball_delta",
        "total_runtime_errors",
        "max_assist_rate",
        "max_side_gap",
    )
    return {key: aggregate[key] for key in keep if key in aggregate}


def record_champion_validation(
    *,
    registry_root: Path,
    validation_evidence_path: Path,
    stage: str,
) -> dict[str, Any]:
    registry_root = registry_root.resolve()
    validation_evidence_path = validation_evidence_path.resolve()
    stage = str(stage).strip().lower()

    if stage not in VALIDATION_STAGE_ORDER:
        raise ValueError(f"unknown champion validation stage: {stage}")
    if stage == "promotion":
        raise ValueError("promotion is recorded by promote_champion, not validation")
    if not validation_evidence_path.is_file():
        raise FileNotFoundError(validation_evidence_path)

    current_path = registry_root / "current.json"
    if not current_path.is_file():
        raise FileNotFoundError(current_path)

    pointer = json.loads(current_path.read_text(encoding="utf-8"))
    if pointer.get("schema") != "haxlab-champion-pointer-v1":
        raise ValueError("unsupported champion pointer schema")

    version_id = str(pointer.get("version_id") or "")
    if not version_id:
        raise ValueError("champion pointer missing version_id")

    evidence = json.loads(
        validation_evidence_path.read_text(encoding="utf-8")
    )
    if evidence.get("validated") is not True:
        raise ValueError("validation evidence does not pass its gate")

    candidate = evidence.get("candidate")
    if isinstance(candidate, dict):
        evidence_version = str(candidate.get("version_id") or "")
        if evidence_version and evidence_version != version_id:
            raise ValueError(
                "validation evidence version mismatch: "
                f"{evidence_version} != {version_id}"
            )

    evidence_sha = _sha256(validation_evidence_path)
    destination_dir = registry_root / "validations" / version_id / stage
    destination = destination_dir / f"{evidence_sha}.json"
    destination_dir.mkdir(parents=True, exist_ok=True)

    if destination.exists():
        if _sha256(destination) != evidence_sha:
            raise ValueError("validation evidence hash collision")
    else:
        shutil.copy2(validation_evidence_path, destination)
        if _sha256(destination) != evidence_sha:
            destination.unlink(missing_ok=True)
            raise ValueError("validation evidence copy checksum mismatch")

    previous_stage = str(pointer.get("validation_stage") or "promotion")
    previous_rank = VALIDATION_STAGE_ORDER.get(previous_stage, -1)
    current_rank = VALIDATION_STAGE_ORDER[stage]
    if previous_rank > current_rank:
        raise ValueError(
            f"refusing champion validation stage downgrade: "
            f"{previous_stage} -> {stage}"
        )

    validated_at = datetime.now(timezone.utc).isoformat()
    record = {
        "schema": "haxlab-champion-validation-record-v1",
        "version_id": version_id,
        "stage": stage,
        "validated_at": validated_at,
        "evidence_path": str(destination),
        "evidence_sha256": evidence_sha,
        "summary": _validation_summary(evidence),
    }
    _atomic_json(
        registry_root / "validations" / version_id / "current.json",
        record,
    )

    updated_pointer = {
        **pointer,
        "validation_stage": stage,
        "validation_evidence_path": str(destination),
        "validation_evidence_sha256": evidence_sha,
        "validation_summary": record["summary"],
        "validation_updated_at": validated_at,
        "updated_at": validated_at,
    }
    _atomic_json(current_path, updated_pointer)

    return {
        "recorded": True,
        "version_id": version_id,
        "stage": stage,
        "evidence_path": str(destination),
        "evidence_sha256": evidence_sha,
        "current_path": str(current_path),
        "summary": record["summary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-champion-registry")
    parser.add_argument(
        "--record-validation",
        type=Path,
        default=None,
        help="Record passing validation evidence for the current champion.",
    )
    parser.add_argument(
        "--validation-stage",
        default=None,
        choices=sorted(
            stage for stage in VALIDATION_STAGE_ORDER if stage != "promotion"
        ),
    )
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--promotion-evidence", type=Path, default=None)
    parser.add_argument("--registry-root", type=Path, required=True)
    parser.add_argument("--candidate-name", default=None)
    parser.add_argument(
        "--code-commit",
        default=os.environ.get("GITHUB_SHA"),
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    if args.record_validation is not None:
        if not args.validation_stage:
            parser.error("--validation-stage is required with --record-validation")
        result = record_champion_validation(
            registry_root=args.registry_root,
            validation_evidence_path=args.record_validation,
            stage=args.validation_stage,
        )
    else:
        if args.model_dir is None:
            parser.error("--model-dir is required for promotion")
        if args.promotion_evidence is None:
            parser.error("--promotion-evidence is required for promotion")
        if not args.candidate_name:
            parser.error("--candidate-name is required for promotion")
        result = promote_champion(
            model_dir=args.model_dir,
            promotion_evidence_path=args.promotion_evidence,
            registry_root=args.registry_root,
            candidate_name=args.candidate_name,
            code_commit=args.code_commit,
        )
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.output is not None:
        _atomic_json(args.output, result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
