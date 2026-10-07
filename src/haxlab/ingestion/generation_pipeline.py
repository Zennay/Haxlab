from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import tempfile

from haxlab.ingestion.generation_store import (
    ResolvedGeneration,
    publish_generation,
)
from haxlab.ingestion.pipeline import run_import
from haxlab.models import ImportManifest


class GenerationImportError(ValueError):
    """Raised when generation-backed M0 import configuration is unsafe."""


@dataclass(frozen=True)
class GenerationImportResult:
    manifest: ImportManifest
    generation: ResolvedGeneration


def _resolved(path: Path, *, strict: bool) -> Path:
    try:
        return Path(path).resolve(strict=strict)
    except OSError as exc:
        raise GenerationImportError(f"cannot resolve path safely: {path}") from exc


def _validate_roots(export_root: Path, store_root: Path) -> None:
    export = _resolved(export_root, strict=True)
    store = _resolved(store_root, strict=False)
    if store == export or store.is_relative_to(export):
        raise GenerationImportError(
            "store_root must be outside the immutable export_root"
        )


def run_generation_import(
    export_root: Path,
    store_root: Path,
    *,
    minimum_match_confidence: float = 0.65,
) -> GenerationImportResult:
    """Build M0 privately, then atomically publish one complete generation."""

    export_root = Path(export_root)
    store_root = Path(store_root)
    _validate_roots(export_root, store_root)

    with tempfile.TemporaryDirectory(prefix="haxlab-m0-build-") as temporary:
        build_root = Path(temporary) / "dataset"
        manifest = run_import(
            export_root,
            build_root,
            minimum_match_confidence=minimum_match_confidence,
        )
        generation = publish_generation(build_root, store_root)

    return GenerationImportResult(
        manifest=manifest,
        generation=generation,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m haxlab.ingestion.generation_pipeline"
    )
    parser.add_argument("export_root", type=Path)
    parser.add_argument("store_root", type=Path)
    parser.add_argument(
        "--minimum-match-confidence",
        type=float,
        default=0.65,
    )
    args = parser.parse_args()

    try:
        result = run_generation_import(
            args.export_root,
            args.store_root,
            minimum_match_confidence=args.minimum_match_confidence,
        )
    except (GenerationImportError, ValueError, RuntimeError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                sort_keys=True,
            )
        )
        return 2

    print(
        json.dumps(
            {
                "ok": True,
                "manifest": result.manifest.as_dict(),
                "generation": {
                    "generation_id": result.generation.generation_id,
                    "receipt_sha256": result.generation.receipt_sha256,
                    "commit_sha256": result.generation.commit_sha256,
                },
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
