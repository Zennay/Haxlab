# Skill output model integrity

The canonical skill-estimation output objects are a fail-closed data-pipeline boundary.

## Contract

- `SkillDimensionEstimate` accepts only native finite `int`/`float` values.
- `uncertainty` and `effective_weight` are non-negative.
- `PlayerSkillEstimate.player_id` is a native non-empty string.
- `dimensions` must be supplied as a plain `dict` with exactly the full `PerformanceVector` field set; after validation it is defensively copied into an immutable mapping view.
- every dimension value is a canonical `SkillDimensionEstimate`.
- `observation_count` is a native non-negative integer.
- aggregate `effective_weight` is finite and non-negative.

These checks protect downstream curriculum/training consumers from malformed producer state without changing estimator or leaderboard semantics.
