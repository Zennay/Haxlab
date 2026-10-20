# Replay ↔ report matcher confidence admission

`match_replays_to_reports(..., minimum_confidence=0.65)` performs greedy one-to-one pairing using attachment filenames, sizes and timestamps as explicit source evidence. Before any pairing starts, the caller-provided minimum confidence must now be an exact native Python integer or float, finite, and within the inclusive range **0–1**.

Negative thresholds, NaN, infinity, unrepresentably large native integers, booleans, strings, subclasses and values above 1 are rejected with `ValueError("invalid_minimum_confidence")` rather than allowing untrustworthy source matching. Boundary values `0` and `1` remain allowed, preserving their intentional literal meaning; valid default 0.65 and existing sorting and source scoring are unchanged.

This change fixes [HaxLab #376](https://github.com/Zennay/Haxlab/issues/376) solely in the real matching consumer. It makes no changes to Discord/replay schemas, archive writers, model/training/promotion or live state. Only product source and this contract are authored; no tests or QA suites created or manually executed. Existing exact-head CI, independent reviewer and protected release gates remain mandatory.
