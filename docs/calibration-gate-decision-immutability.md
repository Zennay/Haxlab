# Calibration gate decision immutability

The calibration gate publishes a decision that is later serialized into the
frozen-policy calibration summary. The decision is already a frozen dataclass,
so its nested sanity evidence must have the same snapshot semantics.

## Invariant

After `decide_calibration_gate()` returns:

- `passed` and `reasons` retain their existing fail-closed semantics;
- `sanity` remains readable with the existing dictionary interface;
- ordinary mapping mutation operations on `sanity` raise `TypeError`;
- re-running the mapping initializer cannot replace the published snapshot;
- `json.dumps()` serializes the mapping without a workflow-side conversion.

This prevents a caller from changing fields such as
`negative_control_discriminated` or `candidate_d_rejected` after the gate
decision was made while leaving `passed` and `reasons` unchanged.

## Boundary

The mapping stays a `dict` subtype intentionally. The canonical calibration
workflow writes `decision.sanity` directly into a JSON document, and changing
that workflow is outside this lane.

The guard covers the normal mutable mapping surface. Deliberately invoking
base-class mutation primitives such as `dict.__setitem__(...)` is reflective
immutability bypass behavior and belongs to the separate evaluation
immutability-bypass contract rather than this value-object boundary.

This lane does not change thresholds, models, champion state, calibration
inputs, promotion authorization, or the canonical Arena workflows.
