# Skill observation evidence integrity

`PerformanceVector` and `SkillObservation` form the typed handoff from derived match evidence into the HaxLab V0 skill estimator. Invalid runtime values must be rejected at that boundary instead of relying on later arithmetic or output-model validation.

## Contract

### PerformanceVector

Every present performance dimension must be an exact native `int` or `float` and must be finite. `None` remains the only representation for missing evidence.

This rejects:

- booleans and string coercions;
- NaN;
- positive or negative Infinity.

The contract deliberately does not impose a numeric range on dimensions because normalized/residual evidence can legitimately be negative and future producers may use a wider finite scale.

### SkillObservation

An observation is valid only when:

- `player_id` is an exact, non-blank string;
- `performance` is an exact `PerformanceVector`;
- `match_quality_weight` is finite and within `[0, 1]`;
- `minutes_or_possessions_weight` is finite and non-negative;
- optional teammate, opponent and state-difficulty context values are finite native numbers;
- optional `role` is an exact, non-blank string.

No estimator weighting or context-adjustment behavior changes in this lane. Current leaderboard-generated observations already satisfy these bounds.

## Failure posture

Malformed evidence fails immediately with deterministic `ValueError` reasons prefixed by either:

- `invalid_performance_vector:`
- `invalid_skill_observation:`

This prevents invalid derived data from reaching weighting, shrinkage and uncertainty calculations.

## Regression evidence

`tests/test_skill_models.py` locks malformed dimension values, invalid quality/exposure weights, invalid context values, invalid role/player identity, and a current leaderboard-compatible healthy observation.

Issue: #373.
