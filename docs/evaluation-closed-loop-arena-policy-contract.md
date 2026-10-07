# Closed-Loop Arena v2 policy validation contract

The Arena v2 policy is part of the live-promotion boundary. The evaluator must never run under malformed policy configuration.

## Contract

Before reading evaluation evidence, `decide_closed_loop_arena()` validates that:
- the policy is a `ClosedLoopArenaPolicy`;
- `policy_version` is a non-empty string;
- `calibrated` is an exact boolean;
- match-count, partner-count and runtime-error controls are native integers with their existing minimums;
- rate-like tolerances and thresholds are finite numbers in `[0, 1]`;
- distance/time tolerances are finite non-negative numbers.

Invalid policy returns a structurally-invalid, behavior-failed, non-promotable decision. It is never evaluated as a weaker policy and never needs malformed payload evidence to be dereferenced.

## Non-goal

This contract does not change or recalibrate any threshold. Existing defaults and valid custom policy values remain valid. Calibration datasets, scenario sources, models and champion state are untouched.
