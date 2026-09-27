from __future__ import annotations

import unittest

import numpy as np

from haxlab.learning.candidate_h import (
    _activation_metrics,
    _fit_activation_head,
    _hidden1,
    _recovery_draw_indices,
    _route_direction_probabilities,
    _runtime_payload,
)


class CandidateHTests(unittest.TestCase):
    def test_recovery_sampling_is_deterministic_and_without_replacement_per_pass(self) -> None:
        first = _recovery_draw_indices(5, 12, seed=1338)
        second = _recovery_draw_indices(5, 12, seed=1338)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(set(first[:5].tolist())), 5)
        self.assertEqual(len(set(first[5:10].tolist())), 5)

    def test_activation_ols_is_deterministic_and_separates_simple_data(self) -> None:
        positive = np.asarray([[3.0, 2.0], [2.0, 3.0], [4.0, 2.0]], dtype=np.float32)
        negative = np.asarray([[-3.0, -2.0], [-2.0, -3.0], [-4.0, -2.0]], dtype=np.float32)
        w1, b1 = _fit_activation_head(positive, negative)
        w2, b2 = _fit_activation_head(positive, negative)
        np.testing.assert_array_equal(w1, w2)
        np.testing.assert_array_equal(b1, b2)
        metrics = _activation_metrics(positive, negative, w1, b1, threshold=0.5)
        self.assertEqual(metrics["recovery_recall"], 1.0)
        self.assertEqual(metrics["normal_specificity"], 1.0)

    def test_gate_off_is_exact_base_and_gate_on_uses_recovery(self) -> None:
        base = np.asarray([[0.1, 0.9], [0.8, 0.2]], dtype=np.float32)
        recovery = np.asarray([[0.7, 0.3], [0.2, 0.8]], dtype=np.float32)
        activation = np.asarray([0.49, 0.50], dtype=np.float32)
        routed, active = _route_direction_probabilities(
            base, recovery, activation, threshold=0.5
        )
        self.assertFalse(bool(active[0]))
        self.assertTrue(bool(active[1]))
        self.assertTrue(np.array_equal(routed[0], base[0]))
        self.assertTrue(np.array_equal(routed[1], recovery[1]))
        self.assertEqual(float(np.max(np.abs(routed[~active] - base[~active]))), 0.0)

    def test_hidden1_preserves_frozen_first_layer_representation(self) -> None:
        base = {
            "w1": np.asarray([[1.0, -1.0], [2.0, 1.0]], dtype=np.float32),
            "b1": np.asarray([0.0, 0.5], dtype=np.float32),
        }
        x = np.asarray([[1.0, 2.0]], dtype=np.float32)
        got = _hidden1(x, base)
        expected = np.maximum(x @ base["w1"] + base["b1"], 0.0)
        np.testing.assert_array_equal(got, expected)

    def test_runtime_serialization_preserves_base_and_adds_isolated_heads(self) -> None:
        base = {
            "schema": "haxlab-elite-js-runtime-v1",
            "weights": {
                "wd": [[1.0, 2.0]],
                "bd": [0.0, 0.0],
                "wk": [[1.0]],
                "bk": [0.0],
                "wf": [[1.0, 2.0]],
                "bf": [0.0, 0.0],
            },
        }
        payload = _runtime_payload(
            base,
            recovery_weight=np.asarray([[3.0, 4.0]], dtype=np.float32),
            recovery_bias=np.asarray([0.1, 0.2], dtype=np.float32),
            activation_weight=np.asarray([[5.0, 6.0]], dtype=np.float32),
            activation_bias=np.asarray([0.3, 0.4], dtype=np.float32),
            threshold=0.5,
            contract_blob="a" * 40,
        )
        self.assertEqual(payload["weights"]["wd"], base["weights"]["wd"])
        self.assertEqual(payload["weights"]["wk"], base["weights"]["wk"])
        self.assertEqual(payload["weights"]["wf"], base["weights"]["wf"])
        self.assertEqual(payload["recovery_routing"]["activation_threshold"], 0.5)
        self.assertEqual(payload["recovery_routing"]["activation_representation"], "h1")
        self.assertIn("recovery_wd", payload["weights"])
        self.assertIn("recovery_activation_w", payload["weights"])
        self.assertNotIn("recovery_wd", base["weights"])


if __name__ == "__main__":
    unittest.main()
