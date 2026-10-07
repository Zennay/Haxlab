from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from haxlab.evaluation.calibration_gate import CALIBRATION_LABELS, EXPECTED_SOURCES
from haxlab.evaluation.calibration_resume import reusable_result_or_reasons


INVENTORY_SCHEMA = "haxlab-calibration-reuse-inventory-v1"
SOURCE_IDS = tuple(f"{index:02d}" for index in range(1, EXPECTED_SOURCES + 1))


def _blocked_row(
    *,
    label: str,
    source_id: str,
    result_path: Path,
    reasons: Sequence[str],
) -> dict[str, Any]:
    return {
        "label": label,
        "source_id": source_id,
        "result": str(result_path),
        "reusable": False,
        "reasons": list(reasons),
    }


def inventory_calibration_results(
    result_root: Path,
    *,
    source_root: Path,
    challengers: Mapping[str, Path],
    champion: Path,
    partners: Sequence[Path],
    seconds: int,
    sample_every: int,
    max_scenarios: int,
    plug_repeats: int,
    seed: int,
) -> dict[str, Any]:
    """Classify an existing calibration batch without mutating its evidence.

    Every expected label/source result is passed through the canonical
    calibration-resume validator. Missing/corrupt evidence and unavailable
    bound inputs are reported as blocked rows rather than escaping as an
    inventory crash.
    """
    result_root = Path(result_root)
    source_root = Path(source_root)

    expected_labels = tuple(CALIBRATION_LABELS)
    missing_labels = [
        label for label in expected_labels if label not in challengers
    ]
    unexpected_labels: list[str] = []
    invalid_challenger_key = False
    for key in challengers:
        if type(key) is not str:
            invalid_challenger_key = True
        elif key not in expected_labels:
            unexpected_labels.append(key)
    unexpected_labels.sort()

    request_errors: list[str] = []
    global_request_errors: list[str] = []
    if missing_labels:
        request_errors.append(
            "challengers:missing:" + ",".join(missing_labels)
        )
    if invalid_challenger_key:
        request_errors.append("challengers:key_type")
        global_request_errors.append("challengers:key_type")
    if unexpected_labels:
        unexpected_reason = (
            "challengers:unexpected:" + ",".join(unexpected_labels)
        )
        request_errors.append(unexpected_reason)
        global_request_errors.append(unexpected_reason)

    rows: list[dict[str, Any]] = []
    per_label: dict[str, dict[str, Any]] = {}

    for label in expected_labels:
        label_rows: list[dict[str, Any]] = []
        challenger = challengers.get(label)
        for source_id in SOURCE_IDS:
            result_path = result_root / f"{label}-source-{source_id}.json"
            blocked_reasons = list(global_request_errors)
            if challenger is None:
                blocked_reasons.append(f"challenger:{label}:missing")
            elif not isinstance(challenger, Path):
                blocked_reasons.append(f"challenger:{label}:not_path")

            if blocked_reasons:
                row = _blocked_row(
                    label=label,
                    source_id=source_id,
                    result_path=result_path,
                    reasons=blocked_reasons,
                )
            else:
                source = source_root / f"source-{source_id}"
                try:
                    reusable, reasons = reusable_result_or_reasons(
                        result_path,
                        challenger=challenger,
                        champion=champion,
                        partners=partners,
                        stadium=source / "stadium.hbs",
                        scenarios=source / "scenarios.json",
                        seconds=seconds,
                        sample_every=sample_every,
                        max_scenarios=max_scenarios,
                        plug_repeats=plug_repeats,
                        seed=seed,
                    )
                except (OSError, TypeError, ValueError) as exc:
                    reusable = False
                    reasons = (f"validator_error:{type(exc).__name__}",)

                if type(reusable) is not bool:
                    reusable = False
                    reasons = ("validator_error:reusable_not_boolean",)
                elif reusable and reasons:
                    reusable = False
                    reasons = (
                        "validator_error:reusable_with_reasons",
                        *tuple(reasons),
                    )

                row = {
                    "label": label,
                    "source_id": source_id,
                    "result": str(result_path),
                    "reusable": reusable,
                    "reasons": list(reasons),
                }

            rows.append(row)
            label_rows.append(row)

        reusable_sources = sum(
            1 for row in label_rows if row["reusable"] is True
        )
        per_label[label] = {
            "expected_sources": EXPECTED_SOURCES,
            "reusable_sources": reusable_sources,
            "blocked_sources": EXPECTED_SOURCES - reusable_sources,
        }

    expected_results = len(expected_labels) * EXPECTED_SOURCES
    reusable_results = sum(1 for row in rows if row["reusable"] is True)
    return {
        "schema": INVENTORY_SCHEMA,
        "result_root": str(result_root),
        "source_root": str(source_root),
        "expected_results": expected_results,
        "reusable_results": reusable_results,
        "blocked_results": expected_results - reusable_results,
        "complete": reusable_results == expected_results,
        "request_errors": request_errors,
        "labels": per_label,
        "results": rows,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only inventory of reusable Arena-v2 calibration source results."
        )
    )
    parser.add_argument("result_root", type=Path)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--champion", required=True, type=Path)
    parser.add_argument("--candidate-d", required=True, type=Path)
    parser.add_argument("--weak-zero", required=True, type=Path)
    parser.add_argument(
        "--partner-model",
        action="append",
        required=True,
        type=Path,
    )
    parser.add_argument("--seconds", required=True, type=int)
    parser.add_argument("--sample-every", required=True, type=int)
    parser.add_argument("--max-scenarios", required=True, type=int)
    parser.add_argument("--plug-repeats", required=True, type=int)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return non-zero unless all nine expected results are reusable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    inventory = inventory_calibration_results(
        args.result_root,
        source_root=args.source_root,
        challengers={
            "champion-self": args.champion,
            "candidate-d": args.candidate_d,
            "weak-zero": args.weak_zero,
        },
        champion=args.champion,
        partners=tuple(args.partner_model),
        seconds=args.seconds,
        sample_every=args.sample_every,
        max_scenarios=args.max_scenarios,
        plug_repeats=args.plug_repeats,
        seed=args.seed,
    )
    print(json.dumps(inventory, indent=2, sort_keys=True))
    if args.require_complete and not inventory["complete"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
