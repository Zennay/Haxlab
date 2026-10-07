# Multisource evaluation integer-config contract

The frozen multisource suite is evaluation evidence. Its manifest must therefore represent the caller's exact declared configuration, not a silently coerced approximation.

## Contract

`build_frozen_multisource_suite()` accepts native Python integers only for:
- `suite_seed`;
- `scenarios_per_source`;
- `rollout_seconds`;
- `sample_every_ticks`;
- `history_window`;
- `minimum_sources`.

Booleans, floats and numeric strings are rejected. Controls with documented lower bounds are rejected below those bounds instead of being silently clamped.

`source_seed()` applies the same strict-integer rule to `suite_seed` and requires `source_index >= 1`.

## Why

Python's `int(...)` accepts values such as `True`, `4.9` and `"4"`. Silent conversion can freeze an evaluation manifest whose recorded settings differ from the caller's actual input, weakening reproducibility and provenance. A promotion/evaluation boundary should fail closed instead.

The CLI remains unchanged because `argparse` already produces native integers for these options.
