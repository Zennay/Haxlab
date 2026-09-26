from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from haxlab.learning.elite import (
    DIR_TO_CLASS,
    RECOVERY_OPTIMIZER_KEYS,
    _init_params,
    _load_recovery_contract,
    _load_recovery_examples,
    _recovery_draw_indices,
    _train_direction_only_batch,
)


class CandidateFRecoveryTests(unittest.TestCase):
    def test_sampling_is_deterministic_and_without_replacement_per_pass(self) -> None:
        first = _recovery_draw_indices(5, 12, seed=1338)
        second = _recovery_draw_indices(5, 12, seed=1338)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(set(first[:5].tolist())), 5)
        self.assertEqual(len(set(first[5:10].tolist())), 5)
    def test_direction_only_gradient_excludes_kick_and_future_heads(self) -> None:
        params = _init_params(
            input_dim=8,
            hidden_dim=6,
            hidden_dim_2=5,
            rng=np.random.default_rng(7),
        )
        x = np.random.default_rng(8).normal(size=(4, 8)).astype(np.float32)
        direction = np.asarray([0, 1, 4, 8], dtype=np.int64)
        grads, loss = _train_direction_only_batch(x, direction, params)

        self.assertEqual(set(grads), set(RECOVERY_OPTIMIZER_KEYS))
        self.assertTrue(np.isfinite(loss))
        for key, grad in grads.items():
            self.assertEqual(grad.shape, params[key].shape)
            self.assertTrue(np.isfinite(grad).all())

    def test_recovery_target_comes_from_role_target_not_failed_action(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "train.jsonl"
            window = [
                {
                    "features": {"own_x": 0.0, "own_y": 0.0},
                    "canonical_action": {"dir_x": -1, "dir_y": 1},
                },
                {
                    "features": {"own_x": 0.0, "own_y": 0.0},
                    "canonical_action": {"dir_x": -1, "dir_y": 1},
                },
            ]
            row = {
                "split": "train",
                "failure_types": ["boundary"],
                "recovery_window": window,
                "auxiliary_targets": {
                    "role_target": {"canonical_x": 20.0, "y": -20.0}
                },
                "role": "gk",
                "state_fingerprint": "a" * 64,
            }
            path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            x, direction, roles, fingerprints = _load_recovery_examples(
                path,
                split="train",
                expected_count=1,
                input_columns=["own_x", "own_y"],
                mean=np.zeros(2, dtype=np.float32),
                std=np.ones(2, dtype=np.float32),
                window=2,
                failure_any_of={"boundary", "role_deviation"},
                deadzone=7,
            )
            self.assertEqual(x.shape, (1, 8))
            self.assertEqual(int(direction[0]), DIR_TO_CLASS[(1, -1)])
            self.assertEqual(int(roles[0]), 0)
            self.assertEqual(fingerprints, ["a" * 64])

    def test_contract_sha_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            payload = {
                "schema": "haxlab-candidate-f-recovery-contract-v1",
                "candidate": "F",
                "frozen_before_training": True,
            }
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            path.write_bytes(raw)
            correct = hashlib.sha256(raw).hexdigest()

            contract, actual = _load_recovery_contract(path, correct)
            self.assertEqual(actual, correct)
            self.assertEqual(contract["candidate"], "F")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                _load_recovery_contract(path, "0" * 64)


if __name__ == "__main__":
    unittest.main()
