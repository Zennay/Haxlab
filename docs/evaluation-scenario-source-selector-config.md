# Scenario-source selector configuration contract

Scenario-source selection freezes the replay inputs used by evaluation. The manifest must therefore reflect the caller's exact selector controls rather than silently normalized values.

## Contract

`select_scenario_source()` requires `max_candidates` to be a native Python integer >= 1.

`select_scenario_sources()` requires both `count` and `max_candidates` to be native Python integers >= 1.

Booleans, floats, numeric strings, zero and negative values are rejected. The CLI passes its parsed integer values through unchanged; it no longer clamps them before the API call.

## Non-goal

This contract does not change replay health criteria, SQL ordering, exclusion semantics, role inference, or the deterministic source ranking algorithm. Valid defaults and valid explicit positive integer values produce the same selection behavior as before.
