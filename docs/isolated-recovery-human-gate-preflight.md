# Isolated-recovery frozen human-gate preflight

Candidate H exposed an evaluation-plumbing failure. Its development smoke passed,
then the sealed human holdout was opened once, but its routed evaluator serialized
only aggregate metrics. The unchanged haxlab.evaluation.elite_gate requires
training provenance, baselines, kick rates, and per-role metrics, so H correctly
failed closed before Promotion-v5.

## Rule for future isolated-recovery candidates

Before any sealed holdout is read, the candidate must:

1. Run its routed evaluator on development validation data.
2. Preserve frozen_holdout_used_for_selection=false and
   kick_threshold_source=validation_only.
3. Call assert_development_evaluator_gate_compatible from
   haxlab.evaluation.elite_gate_preflight.
4. Stop immediately if the preflight fails.
5. Only after this structural/provenance preflight and candidate-specific
   development smoke pass may the sealed holdout be opened.

The preflight is structural only. It does not weaken or duplicate the performance
thresholds in elite_gate. The unchanged frozen gate still makes the final holdout
decision.

Evidence metadata is fail-closed at the type boundary: rate fields must be native
finite numeric values, sample counts must be native integers, and provenance
markers such as `kick_threshold_source` must be native strings. Numeric strings,
booleans, and integer-valued floats are not canonical evidence.

Candidate H remains rejected and must not be retried. Promotion-v5 remains
pristine until a future preregistered challenger reaches it through all earlier
gates.
