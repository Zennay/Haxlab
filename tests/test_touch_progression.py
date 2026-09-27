from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from haxlab.learning.touch_progression import (
    _row_key,
    build_touch_progression_dataset,
)


COLUMNS = ["frame", "player_index", "role_id"]


def _write_index(path: Path, shard_paths: list[Path]) -> None:
    payload = {"entries": [{"shard_path": str(item)} for item in shard_paths]}
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_replay(
    root: Path,
    *,
    replay_sha: str,
    role_id: int,
    replay_player_id: int,
    touch_outcome: int,
    progression: float,
) -> Path:
    shard = root / f"{replay_sha}.f32.gz"
    rows = np.asarray(
        [[float(frame), 0.0, float(role_id)] for frame in range(6, 55, 6)],
        dtype="<f4",
    )
    with gzip.open(shard, "wb") as handle:
        handle.write(rows.tobytes())

    meta = {
        "rowWidth": len(COLUMNS),
        "columns": COLUMNS,
        "selectedPlayers": {"0": "name:test"},
        "selectedReplayPlayers": {
            str(replay_player_id): {
                "identity": "name:test",
                "role": "am",
                "weight": 1.0,
            }
        },
    }
    shard.with_name(f"{replay_sha}.meta.json").write_text(
        json.dumps(meta),
        encoding="utf-8",
    )

    state_path = root / "state" / replay_sha[:2] / replay_sha[2:4] / f"{replay_sha}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "sparseEvents": {
                    "touches": [
                        [
                            60,
                            replay_player_id,
                            1,
                            0.0,
                            0.0,
                            0.0,
                            0,
                            touch_outcome,
                            replay_player_id,
                            progression,
                        ]
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    return shard


def _expected_split(
    *,
    split: str,
    replay_sha: str,
    role_id: int,
    replay_player_id: int,
    touch_outcome_name: str,
    progression: float,
) -> dict:
    target = 1 if progression > 0 else 0
    key = _row_key(
        split,
        replay_sha,
        replay_player_id,
        60,
        54,
        role_id,
        target,
        progression,
    )
    digest = hashlib.sha256((key + "\n").encode("utf-8")).hexdigest()
    roles = {"gk": 0, "dm": 0, "am": 0, "st": 0}
    roles[{0: "gk", 1: "dm", 2: "am", 3: "st"}[role_id]] = 1
    outcomes = {"self_retouch": 0, "teammate": 0, "opponent": 0, "goal": 0}
    outcomes[touch_outcome_name] = 1
    return {
        "replays": 1,
        "rows": 1,
        "positive": target,
        "negative": 1 - target,
        "positive_rate": float(target),
        "by_role": roles,
        "touch_outcomes": outcomes,
        "missing_state_replays": 0,
        "row_fingerprint_digest_sha256": digest,
    }


class TouchProgressionDatasetTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path]:
        train_sha = "a" * 64
        validation_sha = "b" * 64
        holdout_sha = "c" * 64
        train_shard = _write_replay(
            root,
            replay_sha=train_sha,
            role_id=2,
            replay_player_id=7,
            touch_outcome=2,
            progression=10.0,
        )
        validation_shard = _write_replay(
            root,
            replay_sha=validation_sha,
            role_id=3,
            replay_player_id=9,
            touch_outcome=3,
            progression=-5.0,
        )
        train_index = root / "train.json"
        validation_index = root / "validation.json"
        holdout_index = root / "holdout.json"
        _write_index(train_index, [train_shard])
        _write_index(validation_index, [validation_shard])
        _write_index(holdout_index, [root / f"{holdout_sha}.f32.gz"])

        descriptor = {
            "schema": "haxlab-candidate-i-touch-progression-dataset-v1",
            "frozen_before_candidate_i_training": True,
            "source_index_sha256": {
                "train": _sha256(train_index),
                "validation": _sha256(validation_index),
                "holdout_exclusion": _sha256(holdout_index),
            },
            "split_overlap_replays": {
                "train_validation": 0,
                "train_holdout": 0,
                "validation_holdout": 0,
            },
            "rules": {
                "temporal_window_frames": 8,
                "sampled_state_must_end_before_touch": True,
                "pre_touch_min_ticks": 6,
                "pre_touch_max_ticks": 11,
                "max_contiguous_sample_gap_ticks": 18,
                "allowed_touch_outcome_codes": [1, 2, 3, 4],
                "progression_required": True,
                "target": "positive_progression = 1 iff sparseEvents.touches.progression > 0 else 0",
                "class_reweighting": False,
                "role_reweighting": False,
                "state_jitter": False,
            },
            "train": _expected_split(
                split="train",
                replay_sha=train_sha,
                role_id=2,
                replay_player_id=7,
                touch_outcome_name="teammate",
                progression=10.0,
            ),
            "validation": _expected_split(
                split="validation",
                replay_sha=validation_sha,
                role_id=3,
                replay_player_id=9,
                touch_outcome_name="opponent",
                progression=-5.0,
            ),
        }
        descriptor_path = root / "descriptor.json"
        descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
        return descriptor_path, train_index, validation_index, holdout_index

    def test_materializer_matches_frozen_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, train_index, validation_index, holdout_index = self._fixture(root)
            manifest = build_touch_progression_dataset(
                descriptor_path=descriptor,
                train_index_path=train_index,
                validation_index_path=validation_index,
                holdout_index_path=holdout_index,
                state_root=root / "state",
                output_dir=root / "out",
            )
            self.assertEqual(manifest["train"]["rows"], 1)
            self.assertEqual(manifest["validation"]["rows"], 1)
            self.assertFalse(manifest["holdout_materialized"])

            train_row = json.loads((root / "out/train.jsonl").read_text().strip())
            self.assertEqual(train_row["sample_frame"], 54)
            self.assertEqual(train_row["pre_touch_lag_ticks"], 6)
            self.assertEqual(train_row["positive_progression"], 1)

            validation_row = json.loads(
                (root / "out/validation.jsonl").read_text().strip()
            )
            self.assertEqual(validation_row["positive_progression"], 0)
            self.assertEqual(validation_row["role"], "st")

    def test_count_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, train_index, validation_index, holdout_index = self._fixture(root)
            payload = json.loads(descriptor.read_text())
            payload["train"]["rows"] = 2
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "train rows mismatch"):
                build_touch_progression_dataset(
                    descriptor_path=descriptor,
                    train_index_path=train_index,
                    validation_index_path=validation_index,
                    holdout_index_path=holdout_index,
                    state_root=root / "state",
                    output_dir=root / "out",
                )

    def test_replay_overlap_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor, train_index, validation_index, holdout_index = self._fixture(root)
            train_payload = json.loads(train_index.read_text())
            holdout_index.write_text(
                json.dumps({"entries": train_payload["entries"]}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            payload = json.loads(descriptor.read_text())
            payload["source_index_sha256"]["holdout_exclusion"] = _sha256(holdout_index)
            descriptor.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "replay overlap mismatch"):
                build_touch_progression_dataset(
                    descriptor_path=descriptor,
                    train_index_path=train_index,
                    validation_index_path=validation_index,
                    holdout_index_path=holdout_index,
                    state_root=root / "state",
                    output_dir=root / "out",
                )


if __name__ == "__main__":
    unittest.main()
