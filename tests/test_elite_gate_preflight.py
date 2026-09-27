from __future__ import annotations

import copy
import unittest

from haxlab.evaluation.elite_gate_preflight import (
    assert_development_evaluator_gate_compatible,
    assert_elite_gate_evidence_schema,
    development_holdout_schema_proxy,
    elite_gate_evidence_issues,
)


def complete_split() -> dict:
    role = {
        "samples": 100,
        "direction_accuracy": 0.72,
        "kick_f1": 0.2,
        "kick_true_rate": 0.01,
        "kick_predicted_rate": 0.01,
        "baselines": {"majority_direction_accuracy": 0.40},
    }
    return {
        "samples": 400,
        "direction_accuracy": 0.72,
        "kick_f1": 0.2,
        "kick_true_rate": 0.01,
        "kick_predicted_rate": 0.01,
        "baselines": {"majority_direction_accuracy": 0.40},
        "by_role": {
            "gk": copy.deepcopy(role),
            "dm": copy.deepcopy(role),
            "am": copy.deepcopy(role),
            "st": copy.deepcopy(role),
        },
    }


class EliteGateEvidencePreflightTests(unittest.TestCase):
    def test_candidate_h_style_aggregate_metrics_fail_before_holdout(self) -> None:
        metadata = {
            "training": {},
            "final_validation": {"direction_accuracy": 0.73},
            "final_holdout": {
                "samples": 1516384,
                "direction_accuracy": 0.7291,
                "kick_f1": 0.2424,
            },
        }
        issues = elite_gate_evidence_issues(metadata)
        self.assertIn(
            "missing_training.frozen_holdout_used_for_selection", issues
        )
        self.assertIn("missing_training.kick_threshold_source", issues)
        self.assertIn("missing_final_holdout.kick_true_rate", issues)
        self.assertIn("missing_final_holdout.kick_predicted_rate", issues)
        self.assertIn("missing_final_holdout.baselines", issues)
        self.assertIn("missing_final_holdout.by_role", issues)
        self.assertIn("missing_final_holdout.by_role.gk", issues)
        with self.assertRaisesRegex(ValueError, "elite gate evidence preflight"):
            assert_elite_gate_evidence_schema(metadata)

    def test_complete_standard_gate_shape_passes(self) -> None:
        split = complete_split()
        metadata = {
            "training": {
                "frozen_holdout_used_for_selection": False,
                "kick_threshold_source": "validation_only",
            },
            "final_validation": copy.deepcopy(split),
            "final_holdout": copy.deepcopy(split),
        }
        self.assertEqual(elite_gate_evidence_issues(metadata), [])
        assert_elite_gate_evidence_schema(metadata)

    def test_wrong_training_provenance_fails_when_metrics_are_complete(self) -> None:
        split = complete_split()
        metadata = {
            "training": {
                "frozen_holdout_used_for_selection": True,
                "kick_threshold_source": "holdout",
            },
            "final_validation": copy.deepcopy(split),
            "final_holdout": copy.deepcopy(split),
        }
        issues = elite_gate_evidence_issues(metadata)
        self.assertIn(
            "training.frozen_holdout_used_for_selection_must_be_false", issues
        )
        self.assertIn(
            "training.kick_threshold_source_must_be_validation_only", issues
        )

    def test_development_proxy_checks_shape_without_mutating_inputs(self) -> None:
        training = {
            "frozen_holdout_used_for_selection": False,
            "kick_threshold_source": "validation_only",
        }
        validation = complete_split()
        original_training = copy.deepcopy(training)
        original_validation = copy.deepcopy(validation)

        proxy = assert_development_evaluator_gate_compatible(
            training=training,
            validation_metrics=validation,
        )
        self.assertTrue(proxy["schema_preflight_only"])
        self.assertEqual(proxy["final_holdout"], validation)
        self.assertEqual(training, original_training)
        self.assertEqual(validation, original_validation)

        raw_proxy = development_holdout_schema_proxy(
            training=training,
            validation_metrics=validation,
        )
        self.assertEqual(raw_proxy["final_validation"], validation)


if __name__ == "__main__":
    unittest.main()
