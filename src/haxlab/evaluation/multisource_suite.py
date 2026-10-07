from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


MULTISOURCE_SUITE_SCHEMA = "haxlab-multisource-evaluation-suite-v2"
SOURCE_SCHEMA = "haxlab-replay-scenario-source-v1"
SCENARIO_SCHEMA = "haxlab-replay-seeded-scenarios-v1"
SELECTION_ALGORITHM = "sha256-ascending-v1"
REQUIRED_FILES = ("source.json", "stadium.hbs", "scenarios.json")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(payload: dict[str, Any]) -> str:
    rendered = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(rendered).hexdigest()


def _strict_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    return value


def source_seed(suite_seed: int, replay_sha256: str, source_index: int) -> int:
    seed = _strict_int(suite_seed, "suite_seed")
    index = _strict_int(source_index, "source_index", minimum=1)
    material = (
        f"haxlab-multisource-suite-v2\n{seed}\n"
        f"{replay_sha256.lower()}\n{index}"
    ).encode("utf-8")
    # Keep seeds in a portable signed-32-bit range accepted by all current
    # JS/Python arena runners.
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big") & 0x7FFFFFFF


def _load_source_root(root: Path) -> dict[str, Any]:
    root = root.resolve()
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise ValueError(
            f"{root}: missing frozen source file(s): {', '.join(missing)}"
        )

    source = json.loads((root / "source.json").read_text(encoding="utf-8"))
    if not isinstance(source, dict):
        raise ValueError(f"{root}: source.json must contain a JSON object")
    if source.get("schema") != SOURCE_SCHEMA:
        raise ValueError(
            f"{root}: unsupported source schema {source.get('schema')!r}"
        )
    replay_sha = str(source.get("sha256") or "").strip().lower()
    if len(replay_sha) != 64 or any(ch not in "0123456789abcdef" for ch in replay_sha):
        raise ValueError(f"{root}: invalid source replay SHA-256")

    scenarios = json.loads((root / "scenarios.json").read_text(encoding="utf-8"))
    if not isinstance(scenarios, dict):
        raise ValueError(f"{root}: scenarios.json must contain a JSON object")
    if scenarios.get("schema") != SCENARIO_SCHEMA:
        raise ValueError(
            f"{root}: unsupported scenario schema {scenarios.get('schema')!r}"
        )

    scenario_rows = scenarios.get("scenarios")
    if not isinstance(scenario_rows, list):
        raise ValueError(f"{root}: scenarios must be a JSON array")
    if any(not isinstance(row, dict) for row in scenario_rows):
        raise ValueError(f"{root}: every scenario must be a JSON object")

    declared_count = scenarios.get("scenario_count")
    if isinstance(declared_count, bool) or not isinstance(declared_count, int):
        raise ValueError(f"{root}: scenario_count must be an integer")
    if declared_count < 0:
        raise ValueError(f"{root}: scenario_count cannot be negative")
    scenario_count = declared_count
    if scenario_count != len(scenario_rows):
        raise ValueError(
            f"{root}: scenario_count does not match scenarios array"
        )

    return {
        "root": root,
        "replay_sha256": replay_sha,
        "source": source,
        "scenario_count": scenario_count,
        "files_sha256": {
            name: sha256_file(root / name)
            for name in REQUIRED_FILES
        },
    }


def build_frozen_multisource_suite(
    source_roots: Iterable[Path],
    *,
    suite_seed: int = 1337,
    scenarios_per_source: int = 16,
    rollout_seconds: int = 30,
    sample_every_ticks: int = 6,
    history_window: int = 8,
    minimum_sources: int = 3,
) -> dict[str, Any]:
    seed = _strict_int(suite_seed, "suite_seed")
    requested_scenarios = _strict_int(
        scenarios_per_source,
        "scenarios_per_source",
        minimum=1,
    )
    seconds = _strict_int(rollout_seconds, "rollout_seconds", minimum=5)
    sample_every = _strict_int(
        sample_every_ticks,
        "sample_every_ticks",
        minimum=1,
    )
    window = _strict_int(history_window, "history_window", minimum=2)
    required_sources = _strict_int(
        minimum_sources,
        "minimum_sources",
        minimum=1,
    )

    loaded = [_load_source_root(Path(root)) for root in source_roots]
    if len(loaded) < required_sources:
        raise ValueError(
            f"multisource suite requires at least {required_sources} "
            f"sources; got {len(loaded)}"
        )

    # Canonical ordering makes the exact manifest independent from CLI argument
    # order and therefore reproducible across workers.
    loaded.sort(key=lambda row: row["replay_sha256"])
    replay_hashes = [row["replay_sha256"] for row in loaded]
    if len(set(replay_hashes)) != len(replay_hashes):
        raise ValueError("multisource suite contains duplicate replay SHA-256")

    sources: list[dict[str, Any]] = []
    for index, row in enumerate(loaded, start=1):
        if row["scenario_count"] < requested_scenarios:
            raise ValueError(
                f"source {row['replay_sha256']} has only "
                f"{row['scenario_count']} scenarios; {requested_scenarios} required"
            )
        descriptor = row["source"]
        sources.append(
            {
                "id": f"source-{index:02d}",
                "replay_sha256": row["replay_sha256"],
                "arena_seed": source_seed(
                    seed,
                    row["replay_sha256"],
                    index,
                ),
                "scenario_count_available": row["scenario_count"],
                "scenario_count_frozen": requested_scenarios,
                "duration_seconds": descriptor.get("duration_seconds"),
                "sampled_states": descriptor.get("sampled_states"),
                "touches": descriptor.get("touches"),
                "role_confidence_min": descriptor.get("role_confidence_min"),
                "files_sha256": row["files_sha256"],
            }
        )

    manifest: dict[str, Any] = {
        "schema": MULTISOURCE_SUITE_SCHEMA,
        "frozen": True,
        "selection_algorithm": SELECTION_ALGORITHM,
        "suite_seed": seed,
        "source_count": len(sources),
        "replay_sha256s": [source["replay_sha256"] for source in sources],
        "evaluation": {
            "scenarios_per_source": requested_scenarios,
            "rollout_seconds": seconds,
            "sample_every_ticks": sample_every,
            "history_window": window,
            "mirror_sides": True,
            "raw_policy_only": True,
        },
        "total_scenarios": len(sources) * requested_scenarios,
        "total_mirrored_matches": len(sources) * requested_scenarios * 2,
        "sources": sources,
    }
    manifest["suite_sha256"] = canonical_sha256(manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-freeze-multisource-suite")
    parser.add_argument(
        "source_roots",
        nargs="+",
        type=Path,
        help="Frozen source directories containing source.json, stadium.hbs and scenarios.json.",
    )
    parser.add_argument("--seed", type=int, default=1337)
    parser.add_argument("--scenarios-per-source", type=int, default=16)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--sample-every", type=int, default=6)
    parser.add_argument("--history-window", type=int, default=8)
    parser.add_argument("--minimum-sources", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = build_frozen_multisource_suite(
        args.source_roots,
        suite_seed=args.seed,
        scenarios_per_source=args.scenarios_per_source,
        rollout_seconds=args.seconds,
        sample_every_ticks=args.sample_every,
        history_window=args.history_window,
        minimum_sources=args.minimum_sources,
    )
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
