# V0 skill estimator prior contract

The V0 skill estimator uses a shrinkage prior for every performance dimension. Those prior values are runtime configuration and must be valid before any weighted arithmetic begins.

## Contract

`estimate_player_skill_v0()` requires:

- `prior_mean`: exact native `int` or `float`, finite;
- `prior_weight`: exact native `int` or `float`, finite, strictly greater than zero.

Booleans, strings, NaN and Infinity are rejected. A zero or negative prior weight is rejected even when observations are present because the estimator relies on a positive prior to keep every dimension defined when evidence is missing.

## Compatibility

The production/default values remain unchanged:

- default `prior_mean=0.0`;
- default `prior_weight=5.0`;
- the current leaderboard producer's explicit `prior_weight=8.0`.

This lane does not alter observation weighting, context adjustment, shrinkage math, output rounding, or uncertainty semantics.

## Failure posture

Malformed prior configuration raises deterministic `ValueError` reasons before estimator arithmetic:

- `invalid_skill_estimator:prior_mean`
- `invalid_skill_estimator:prior_weight`

Focused regressions live in `tests/test_skill.py`.

Issue: #374.
