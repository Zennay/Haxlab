from __future__ import annotations

import copy
import unittest

import numpy as np

from haxlab.learning.candidate_i import (
    EXPECTED_HUMAN_BATCHES_PER_EPOCH,
    EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH,
    _assert_human_epoch_cardinality,
    _binary_auc,
    _human_floor_issues,
    _init_progression_head,
    _progression_batch_grads,
    _progression_draw_indices,
    _runtime_payload,
)


class CandidateITrainingContractTests(unittest.TestCase):
    def test_aux_sampling_is_exact_deterministic_and_without_replacement(self) -> None:
        first = _progression_draw_indices(
            row_count=119184,
            seed=1337,
            epoch=1,
            draws_per_epoch=14336,
        )
        second = _progression_draw_indices(
            row_count=119184,
            seed=1337,
            epoch=1,
            draws_per_epoch=14336,
        )
        other_epoch = _progression_draw_indices(
            row_count=119184,
            seed=1337,
            epoch=2,
            draws_per_epoch=14336,
        )
        self.assertEqual(len(first), 56 * 256)
        self.assertEqual(len(np.unique(first)), len(first))
        np.testing.assert_array_equal(first, second)
        self.assertFalse(np.array_equal(first, other_epoch))

    def test_human_epoch_cardinality_matches_frozen_candidate_e(self) -> None:
        _assert_human_epoch_cardinality(
            epoch=1,
            batches=EXPECTED_HUMAN_BATCHES_PER_EPOCH,
            sequence_samples=EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH,
        )
        with self.assertRaisesRegex(ValueError, "human batch count mismatch"):
            _assert_human_epoch_cardinality(
                epoch=1,
                batches=EXPECTED_HUMAN_BATCHES_PER_EPOCH - 1,
                sequence_samples=EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH,
            )
        with self.assertRaisesRegex(ValueError, "human sequence count mismatch"):
            _assert_human_epoch_cardinality(
                epoch=1,
                batches=EXPECTED_HUMAN_BATCHES_PER_EPOCH,
                sequence_samples=EXPECTED_HUMAN_SEQUENCE_SAMPLES_PER_EPOCH - 1,
            )

    def test_aux_gradients_only_cover_shared_backbone_and_progression_head(self) -> None:
        rng = np.random.default_rng(5)
        params = {
            "w1": rng.normal(size=(7, 5)).astype(np.float32),
            "b1": np.zeros(5, dtype=np.float32),
            "w2": rng.normal(size=(5, 4)).astype(np.float32),
            "b2": np.zeros(4, dtype=np.float32),
            "wd": rng.normal(size=(4, 9)).astype(np.float32),
            "bd": np.zeros(9, dtype=np.float32),
            "wk": rng.normal(size=(4, 1)).astype(np.float32),
            "bk": np.zeros(1, dtype=np.float32),
            "wf": rng.normal(size=(4, 9)).astype(np.float32),
            "bf": np.zeros(9, dtype=np.float32),
        }
        progression = _init_progression_head(hidden_dim_2=4, seed=1337)
        x = rng.normal(size=(16, 7)).astype(np.float32)
        y = np.asarray([0, 1] * 8, dtype=np.int64)
        frozen_before = {
            key: params[key].copy()
            for key in ("wd", "bd", "wk", "bk", "wf", "bf")
        }
        grads, loss = _progression_batch_grads(x, y, params, progression)
        self.assertTrue(np.isfinite(loss))
        self.assertEqual(
            set(grads),
            {
                "w1", "b1", "w2", "b2", "progression_w", "progression_b"
            },
        )
        for key, before in frozen_before.items():
            np.testing.assert_array_equal(params[key], before)

    def test_runtime_payload_never_serializes_progression_head(self) -> None:
        rng = np.random.default_rng(7)
        params = {
            "w1": rng.normal(size=(7, 5)).astype(np.float32),
            "b1": np.zeros(5, dtype=np.float32),
            "w2": rng.normal(size=(5, 4)).astype(np.float32),
            "b2": np.zeros(4, dtype=np.float32),
            "wd": rng.normal(size=(4, 9)).astype(np.float32),
            "bd": np.zeros(9, dtype=np.float32),
            "wk": rng.normal(size=(4, 1)).astype(np.float32),
            "bk": np.zeros(1, dtype=np.float32),
            "wf": rng.normal(size=(4, 9)).astype(np.float32),
            "bf": np.zeros(9, dtype=np.float32),
        }
        runtime = _runtime_payload(
            params=params,
            mean=np.zeros(1, dtype=np.float32),
            std=np.ones(1, dtype=np.float32),
            input_columns=["x"],
            window=3,
            future_horizon_steps=5,
            kick_threshold=0.5,
            kick_thresholds_by_role={0: 0.5, 1: 0.5, 2: 0.5, 3: 0.5},
        )
        self.assertFalse(
            any("progression" in key for key in runtime["weights"])
        )
        self.assertNotIn("progression_routing", runtime)

    def test_human_validation_floors_fail_closed(self) -> None:
        floors = {
            "direction_accuracy": 0.70,
            "joint_accuracy": 0.69,
            "kick_f1": 0.20,
            "future_direction_accuracy": 0.60,
            "by_role_direction_accuracy": {
                "gk": 0.70,
                "dm": 0.70,
                "am": 0.70,
                "st": 0.70,
            },
        }
        metrics = {
            "direction_accuracy": 0.71,
            "joint_accuracy": 0.70,
            "kick_f1": 0.21,
            "future_direction_accuracy": 0.61,
            "by_role": {
                role: {"direction_accuracy": 0.71}
                for role in ("gk", "dm", "am", "st")
            },
        }
        self.assertEqual(_human_floor_issues(metrics, floors), [])
        broken = copy.deepcopy(metrics)
        broken["by_role"]["st"]["direction_accuracy"] = 0.69
        issues = _human_floor_issues(broken, floors)
        self.assertEqual(len(issues), 1)
        self.assertIn("by_role.st.direction_accuracy", issues[0])

    def test_binary_auc_handles_ties(self) -> None:
        labels = np.asarray([0, 0, 1, 1], dtype=np.int64)
        self.assertAlmostEqual(
            _binary_auc(labels, np.asarray([0.1, 0.2, 0.8, 0.9])),
            1.0,
        )
        self.assertAlmostEqual(
            _binary_auc(labels, np.asarray([0.5, 0.5, 0.5, 0.5])),
            0.5,
        )


if __name__ == "__main__":
    unittest.main()
