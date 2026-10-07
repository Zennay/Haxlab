# Replay/report matcher confidence contract

The M0 replay/report matcher uses a caller-provided minimum confidence threshold to decide which candidate pairs may enter the greedy one-to-one assignment. That threshold is part of the data-integrity boundary.

## Contract

`match_replays_to_reports(..., minimum_confidence=...)` accepts only an exact native `int` or `float` that is finite and inside the closed interval `[0, 1]`.

Rejected values include:

- negative thresholds;
- values greater than one;
- NaN and positive/negative Infinity;
- booleans;
- string/other coercible values.

Invalid configuration raises `ValueError("invalid_minimum_confidence")` before any candidate scoring or pairing.

## Why this matters

A negative threshold makes a score-0 replay/report pair eligible even when there is no filename, size or timestamp evidence. That converts configuration corruption into arbitrary greedy pairings. Failing at the threshold boundary prevents those false source associations from entering later M0 publication.

The valid endpoints keep literal semantics:

- `0.0` permits score-0 pairs when a caller intentionally requests that behavior;
- `1.0` requires a fully saturated score;
- the production default remains `0.65`.

No scoring weights, evidence rules or greedy ordering change in this lane.

Issue: #376.
