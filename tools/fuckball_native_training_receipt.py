"""Fail-closed receipt for 4v4 HaxLab candidates trained in FuckBall's native engine.

The native engine generates rollouts; this module only checks resulting evidence. It
never promotes a model or changes HaxLab's live champion or replay library.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import struct
import subprocess
from pathlib import Path

ENGINE_SHA256 = "a0f4dbef90090f6b5c85bc48d4645e8cd3159023bc91af2b8de8225da6840a4b"
BFF_SHA256 = "48772a9e29cecb28a4bd67d6ac360f87d246fe0e1d8008b12eaeeb6ab0b7bed4"
MAX_SHARD_BYTES = 64 * 1024 * 1024
SPLITS = ("train", "validation", "test")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _file(root: Path, relative: str) -> Path:
    if not relative or ".." in Path(relative).parts:
        raise ValueError("artifact path escapes training root")
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("missing, symlinked or out-of-root training artifact")
    return path


def _shard_rows(path: Path, width: int) -> int:
    if width < 7 or width > 128:
        raise ValueError("invalid shard width")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(MAX_SHARD_BYTES + 1)
    if not raw or len(raw) > MAX_SHARD_BYTES or len(raw) % (4 * width):
        raise ValueError("empty, oversized or misaligned float32 shard")
    if any(not math.isfinite(v[0]) for v in struct.iter_unpack("<f", raw)):
        raise ValueError("nonfinite rollout features")
    return len(raw) // (4 * width)


def validate_artifacts(root: Path, episodes: int, engine_commit: str,
                       engine_hash: str, stadium_hash: str, seed: int) -> dict:
    """Validate fixed episode splits and side-swapped arena before sealing receipt."""
    root = root.resolve()
    if not 6 <= episodes <= 200 or not re.fullmatch(r"[0-9a-f]{40}", engine_commit):
        raise ValueError("invalid training configuration")
    if type(seed) is not int or not 0 <= seed <= 0xffffffff:
        raise ValueError("invalid training seed")
    splits = {}
    seen = set()
    stadium_counts = {"BFF": 0, "Classic": 0}
    rows_by_split = {}
    for split in SPLITS:
        entries = _json(_file(root, split + ".json"))["entries"]
        if not isinstance(entries, list) or not entries:
            raise ValueError("missing split entries")
        split_episodes = []
        total_rows = 0
        for entry in entries:
            episode = entry["episode"]
            if type(episode) is not int or episode not in range(episodes) or episode in seen:
                raise ValueError("invalid or overlapping episode")
            expected_split = "validation" if episode % 6 == 4 else "test" if episode % 6 == 5 else "train"
            stadium = "Classic" if episode % 3 == 2 else "BFF"
            if split != expected_split or entry["stadium"] != stadium:
                raise ValueError("non-disjoint split or unexpected stadium")
            expected_shard = "match-" + str(episode) + ".f32.gz"
            shard = _file(root, expected_shard)
            if Path(entry["shard_path"]).resolve() != shard.resolve():
                raise ValueError("shard provenance path mismatch")
            meta = _json(_file(root, "match-" + str(episode) + ".meta.json"))
            columns = meta["columns"]
            width = meta["rowWidth"]
            if (meta.get("schema") != "haxlab-imitation-shard-v2"
                    or meta.get("episode") != episode or meta.get("stadium") != stadium
                    or meta.get("source") != "native-engine-4v4-dagger"
                    or meta.get("sampleStride") != 4
                    or type(width) is not int or not isinstance(columns, list)
                    or len(columns) != width or len(set(columns)) != width
                    or columns[:3] != ["frame", "player_index", "team_id"]
                    or columns[-3:] != ["dir_x", "dir_y", "kick"]):
                raise ValueError("invalid native-engine rollout schema")
            total_rows += _shard_rows(shard, width)
            stadium_counts[stadium] += 1
            split_episodes.append(episode)
            seen.add(episode)
        splits[split] = sorted(split_episodes)
        rows_by_split[split] = total_rows
    if seen != set(range(episodes)) or not all(stadium_counts.values()):
        raise ValueError("incomplete episodes or stadium coverage")

    candidate = _json(_file(root, "candidate.json"))
    tactics = _json(_file(root, "team-policy.json"))
    metrics = _json(_file(root, "weights/metrics.json"))
    benchmark = _json(_file(root, "arena.json"))
    _file(root, "weights/model.npz")
    if candidate.get("schema") != "fuckball-policy-v1":
        raise ValueError("invalid candidate model")
    if (tactics.get("schema") != "fuckball-team-policy-v1"
            or not isinstance(tactics.get("training"), dict)
            or tactics["training"].get("seed") != seed):
        raise ValueError("invalid team tactics")
    if metrics.get("source_kind") != "native-engine-4v4-dagger" or not isinstance(metrics.get("untouched_test"), dict):
        raise ValueError("missing independent held-out test")
    if benchmark.get("schema") != "fuckball-arena-benchmark-v1" or benchmark.get("opponent") != "tactical":
        raise ValueError("invalid native-engine arena evidence")
    matches = benchmark.get("matches")
    if not isinstance(matches, list) or len(matches) != 4:
        raise ValueError("require four side-swapped native-engine matches")
    expected = {(stadium, side) for stadium in ("BFF Big v4", "Classic") for side in (1, 2)}
    actual = {(m.get("map"), m.get("model_team")) for m in matches if isinstance(m, dict)}
    if actual != expected or any(
        type(m.get("model_goals")) is not int or m["model_goals"] < 0
        or type(m.get("tactical_goals")) is not int or m["tactical_goals"] < 0
        or not isinstance(m.get("simulated_seconds"), (int, float))
        or not math.isfinite(m["simulated_seconds"]) or m["simulated_seconds"] <= 0
        for m in matches
    ):
        raise ValueError("incomplete/invalid arena sides, goals or simulated time")
    artifacts = {}
    for item in sorted(root.rglob("*")):
        if item.is_symlink():
            raise ValueError("symlinked training evidence")
        if item.is_file() and item.name != "receipt.json":
            artifacts[str(item.relative_to(root))] = _sha256(item)
    return {
        "schema": "haxlab-fuckball-native-selfplay-receipt-v1",
        "status": "candidate_only",
        "engine_commit": engine_commit,
        "engine_sha256": engine_hash,
        "stadium_sha256": stadium_hash,
        "episodes": episodes,
        "seed": seed,
        "split_episodes": splits,
        "split_rows": rows_by_split,
        "stadium_episodes": stadium_counts,
        "arena_goal_difference": sum(m["model_goals"] - m["tactical_goals"] for m in matches),
        "arena": matches,
        "artifacts_sha256": artifacts,
        "promotion_allowed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--engine-repo", type=Path, required=True)
    parser.add_argument("--engine-commit", required=True)
    parser.add_argument("--episodes", type=int, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    repo = args.engine_repo.resolve(strict=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if commit != args.engine_commit:
        raise ValueError("unexpected FuckBall source revision")
    engine = _file(repo, "dist/vendor/haxball.js")
    stadium = _file(repo, "stadiums/BFF Big v4.hbs")
    engine_hash, stadium_hash = _sha256(engine), _sha256(stadium)
    if engine_hash != ENGINE_SHA256 or stadium_hash != BFF_SHA256:
        raise ValueError("physics or original BFF stadium fingerprint mismatch")
    receipt = validate_artifacts(args.root, args.episodes, commit, engine_hash, stadium_hash, args.seed)
    destination = args.root / "receipt.json"
    if destination.is_symlink():
        raise ValueError("symlinked receipt destination")
    destination.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"receipt": str(destination), "seed": receipt["seed"], "rows": receipt["split_rows"],
                      "arena_goal_difference": receipt["arena_goal_difference"],
                      "promotion_allowed": False}))


if __name__ == "__main__":
    main()
