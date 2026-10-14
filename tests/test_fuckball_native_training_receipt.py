"""Synthetic evidence checks; never run models or mutate production state."""
import gzip
import importlib.util
import json
import struct
import tempfile
import unittest
from pathlib import Path

# HaxLab's pytest.ini puts src/ (not the repository root) on sys.path.
# Load this standalone CLI by explicit path without widening all tests' imports.
MODULE = Path(__file__).resolve().parents[1] / "tools/fuckball_native_training_receipt.py"
SPEC = importlib.util.spec_from_file_location("fuckball_native_training_receipt", MODULE)
assert SPEC is not None and SPEC.loader is not None
RECEIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RECEIPT)
validate_artifacts = RECEIPT.validate_artifacts


class NativeTrainingReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.columns = ["frame", "player_index", "team_id", "own_x", "own_y",
                        "dir_x", "dir_y", "kick"]
        splits = {"train": [], "validation": [], "test": []}
        for i in range(6):
            stadium = "Classic" if i % 3 == 2 else "BFF"
            split = "validation" if i % 6 == 4 else "test" if i % 6 == 5 else "train"
            shard = self.root / f"match-{i}.f32.gz"
            shard.write_bytes(gzip.compress(struct.pack("<8f", 0, 1, 1, 0, 1, 0, 0, 1)))
            self.put(f"match-{i}.meta.json", {
                "schema": "haxlab-imitation-shard-v2",
                "columns": self.columns, "rowWidth": 8,
                "stadium": stadium, "source": "native-engine-4v4-dagger",
                "episode": i,
            })
            splits[split].append({"episode": i, "stadium": stadium, "shard_path": str(shard)})
        for split, entries in splits.items():
            self.put(split + ".json", {"entries": entries})
        self.put("candidate.json", {"schema": "fuckball-policy-v1"})
        self.put("team-policy.json", {"schema": "fuckball-team-policy-v1"})
        (self.root / "weights").mkdir()
        (self.root / "weights/model.npz").write_bytes(b"test-fixture")
        self.put("weights/metrics.json", {"source_kind": "native-engine-4v4-dagger",
                                          "untouched_test": {"direction_accuracy": 0.25}})
        self.put("arena.json", {
            "schema": "fuckball-arena-benchmark-v1", "opponent": "tactical",
            "matches": [
                {"map": stadium, "model_team": team, "model_goals": 1,
                 "tactical_goals": 0, "simulated_seconds": 10}
                for stadium in ("BFF Big v4", "Classic") for team in (1, 2)
            ],
        })

    def put(self, name, payload):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload), encoding="utf-8")

    def check(self):
        return validate_artifacts(self.root, 6, "a" * 40, "engine-hash", "bff-hash")

    def test_accepts_genuine_two_map_disjoint_split_evidence(self):
        result = self.check()
        self.assertEqual(result["status"], "candidate_only")
        self.assertEqual(result["split_episodes"], {
            "train": [0, 1, 2, 3], "validation": [4], "test": [5]})
        self.assertEqual(result["stadium_episodes"], {"BFF": 4, "Classic": 2})
        self.assertEqual(result["arena_goal_difference"], 4)
        self.assertFalse(result["promotion_allowed"])
        self.assertIn("match-0.f32.gz", result["artifacts_sha256"])

    def test_rejects_heldout_episode_leakage(self):
        index = json.loads((self.root / "train.json").read_text())
        index["entries"].append(index["entries"][0])
        self.put("train.json", index)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.check()

    def test_rejects_wrong_stadium(self):
        index = json.loads((self.root / "test.json").read_text())
        index["entries"][0]["stadium"] = "BFF"
        self.put("test.json", index)
        with self.assertRaisesRegex(ValueError, "stadium"):
            self.check()

    def test_rejects_partial_or_repeated_arena(self):
        arena = json.loads((self.root / "arena.json").read_text())
        arena["matches"][3] = arena["matches"][0]
        self.put("arena.json", arena)
        with self.assertRaisesRegex(ValueError, "arena"):
            self.check()

    def test_rejects_corrupt_rollout(self):
        (self.root / "match-1.f32.gz").write_bytes(gzip.compress(b"wrong-size"))
        with self.assertRaisesRegex(ValueError, "misaligned"):
            self.check()

    def test_rejects_nonfinite_features(self):
        (self.root / "match-1.f32.gz").write_bytes(
            gzip.compress(struct.pack("<8f", 0, 1, 1, float("nan"), 1, 0, 0, 1)))
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            self.check()

    def test_rejects_symlinked_evidence(self):
        (self.root / "candidate.json").unlink()
        (self.root / "candidate.json").symlink_to(self.root / "team-policy.json")
        with self.assertRaisesRegex(ValueError, "symlinked"):
            self.check()


if __name__ == "__main__":
    unittest.main()
