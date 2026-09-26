from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from haxlab.evaluation.champion_gate import decide_champion_promotion
from haxlab.evaluation.duel_gate import decide_duel_gate


REGISTRY_SCHEMA = "haxlab-elite-champion-registry-v1"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": REGISTRY_SCHEMA,
            "current": None,
            "history": [],
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != REGISTRY_SCHEMA:
        raise ValueError(
            f"Unsupported champion registry schema: {payload.get('schema')!r}"
        )
    payload.setdefault("current", None)
    payload.setdefault("history", [])
    return payload


def _copy_candidate_artifacts(
    *,
    candidate_id: str,
    candidate_model_dir: Path,
    evidence_paths: dict[str, Path],
    champions_root: Path,
) -> Path:
    destination = champions_root / candidate_id
    model_destination = destination / "model"
    if destination.exists():
        raise FileExistsError(
            f"Champion candidate destination already exists: {destination}"
        )
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(candidate_model_dir, model_destination)
    evidence_destination = destination / "evidence"
    evidence_destination.mkdir(parents=True, exist_ok=True)
    for name, source in evidence_paths.items():
        if source.exists():
            shutil.copy2(source, evidence_destination / f"{name}.json")
    return destination


def evaluate_candidate(
    *,
    metrics: dict[str, Any],
    sandbox: dict[str, Any],
    duel: dict[str, Any] | None,
    has_current_champion: bool,
) -> dict[str, Any]:
    base = decide_champion_promotion(metrics, sandbox)
    reasons = list(base["reasons"])
    eligible = bool(base["eligible_for_champion_promotion"])
    duel_result: dict[str, Any] | None = None

    if has_current_champion:
        if duel is None:
            eligible = False
            reasons.append("duel_required_for_existing_champion")
        else:
            decision = decide_duel_gate(duel)
            duel_result = {
                "eligible_to_replace_champion": (
                    decision.eligible_to_replace_champion
                ),
                "reasons": list(decision.reasons),
                "checks": decision.checks,
            }
            if decision.eligible_to_replace_champion:
                reasons.append("duel_gate_passed")
            else:
                eligible = False
                reasons.extend(
                    f"duel:{reason}" for reason in decision.reasons
                )
    elif duel is not None:
        decision = decide_duel_gate(duel)
        duel_result = {
            "eligible_to_replace_champion": (
                decision.eligible_to_replace_champion
            ),
            "reasons": list(decision.reasons),
            "checks": decision.checks,
        }

    return {
        "eligible_for_promotion": eligible,
        "reasons": reasons,
        "offline_and_sandbox": base,
        "duel": duel_result,
    }


def promote_candidate(
    *,
    registry_path: Path,
    champions_root: Path,
    candidate_id: str,
    candidate_model_dir: Path,
    metrics_path: Path,
    sandbox_path: Path,
    duel_path: Path | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    registry = load_registry(registry_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    sandbox = json.loads(sandbox_path.read_text(encoding="utf-8"))
    duel = (
        json.loads(duel_path.read_text(encoding="utf-8"))
        if duel_path is not None
        else None
    )

    current = registry.get("current")
    evaluation = evaluate_candidate(
        metrics=metrics,
        sandbox=sandbox,
        duel=duel,
        has_current_champion=current is not None,
    )
    if not evaluation["eligible_for_promotion"]:
        return {
            "schema": "haxlab-elite-promotion-result-v1",
            "promoted": False,
            "candidate_id": candidate_id,
            "current_champion": current,
            "evaluation": evaluation,
        }

    required = ("model.npz", "metrics.json", "runtime-model.json")
    missing = [
        name
        for name in required
        if not (candidate_model_dir / name).exists()
    ]
    if missing:
        raise FileNotFoundError(
            "Candidate model directory is missing required artifacts: "
            + ", ".join(missing)
        )

    evidence_paths = {
        "metrics": metrics_path,
        "sandbox": sandbox_path,
    }
    if duel_path is not None:
        evidence_paths["duel"] = duel_path

    promoted_at = datetime.now(timezone.utc).isoformat()
    destination = _copy_candidate_artifacts(
        candidate_id=candidate_id,
        candidate_model_dir=candidate_model_dir,
        evidence_paths=evidence_paths,
        champions_root=champions_root,
    )
    record = {
        "candidate_id": candidate_id,
        "promoted_at": promoted_at,
        "source_ref": source_ref,
        "artifact_dir": str(destination),
        "model_dir": str(destination / "model"),
        "replaced_champion": current,
        "evaluation": evaluation,
    }

    registry["history"].append(record)
    registry["current"] = {
        "candidate_id": candidate_id,
        "promoted_at": promoted_at,
        "source_ref": source_ref,
        "artifact_dir": str(destination),
        "model_dir": str(destination / "model"),
    }
    _atomic_json(registry_path, registry)
    _atomic_json(
        champions_root / "current.json",
        registry["current"],
    )

    return {
        "schema": "haxlab-elite-promotion-result-v1",
        "promoted": True,
        "candidate_id": candidate_id,
        "previous_champion": current,
        "current_champion": registry["current"],
        "evaluation": evaluation,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-elite-promote")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--champions-root", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-model-dir", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--sandbox", type=Path, required=True)
    parser.add_argument("--duel", type=Path, default=None)
    parser.add_argument("--source-ref", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    result = promote_candidate(
        registry_path=args.registry,
        champions_root=args.champions_root,
        candidate_id=args.candidate_id,
        candidate_model_dir=args.candidate_model_dir,
        metrics_path=args.metrics,
        sandbox_path=args.sandbox,
        duel_path=args.duel,
        source_ref=args.source_ref,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result["promoted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())