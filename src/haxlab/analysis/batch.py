from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from haxlab.analysis.v1 import ANALYTICS_SCHEMA, summarize_replay_v1


BATCH_SCHEMA = "haxlab-analytics-batch-v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _output_path(output_root: Path, replay_sha256: str) -> Path:
    prefix = replay_sha256[:2] if len(replay_sha256) >= 2 else "__"
    return output_root / prefix / f"{replay_sha256}.json"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(path)


def build_analytics_batch(
    source_root: Path,
    output_root: Path,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    output_root = output_root.resolve()

    candidates = sorted(
        path
        for path in source_root.rglob("*.json")
        if path.is_file()
    )
    if limit is not None:
        candidates = candidates[: max(0, limit)]

    entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    built = 0
    reused = 0

    for source_path in candidates:
        relative_source = source_path.relative_to(source_root).as_posix()
        replay_sha256 = source_path.stem
        try:
            raw = source_path.read_bytes()
            analysis_sha256 = _sha256(raw)
            destination = _output_path(output_root, replay_sha256)

            status = "built"
            if destination.exists():
                try:
                    existing = json.loads(destination.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    existing = None
                if (
                    isinstance(existing, dict)
                    and existing.get("schema") == ANALYTICS_SCHEMA
                    and (existing.get("source") or {}).get("replay_sha256")
                    == replay_sha256
                    and (existing.get("source") or {}).get("analysis_sha256")
                    == analysis_sha256
                ):
                    status = "reused"

            if status == "built":
                payload = json.loads(raw)
                summary = summarize_replay_v1(payload)
                summary["source"].update(
                    {
                        "replay_sha256": replay_sha256,
                        "analysis_sha256": analysis_sha256,
                        "analysis_relative_path": relative_source,
                    }
                )
                _write_json(destination, summary)
                built += 1
            else:
                reused += 1

            entries.append(
                {
                    "replay_sha256": replay_sha256,
                    "analysis_sha256": analysis_sha256,
                    "source": relative_source,
                    "output": destination.relative_to(output_root).as_posix(),
                    "status": status,
                }
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            failures.append(
                {
                    "source": relative_source,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    manifest = {
        "schema": BATCH_SCHEMA,
        "analytics_schema": ANALYTICS_SCHEMA,
        "source_root": str(source_root),
        "output_root": str(output_root),
        "scanned": len(candidates),
        "succeeded": len(entries),
        "built": built,
        "reused": reused,
        "failed": len(failures),
        "entries": entries,
        "failures": failures,
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-analytics")
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Versioned replay-analysis root, e.g. state-pass-v4.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        required=True,
        help="Separate versioned analytics output root.",
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    manifest = build_analytics_batch(
        args.source_root,
        args.output_root,
        limit=args.limit,
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 1 if manifest["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
