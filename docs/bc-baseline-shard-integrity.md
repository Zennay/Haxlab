# BC baseline shard-integrity contract

The behavioral-cloning baseline consumes generated float32 shards as training evidence. A training consumer must not silently reinterpret malformed metadata or propagate non-finite payload values into normalization, model parameters, or published metrics.

## Contract

Before a shard is used by normalization, evaluation, or training:

1. `rowWidth` is an exact native positive integer.
2. `columns` is a non-empty list of unique, non-empty native strings.
3. `rowWidth` exactly equals the number of declared columns.
4. The decompressed payload contains a whole number of rows for that width.
5. Every float32 value is finite; NaN and positive/negative Infinity fail closed.
6. `dir_x` and `dir_y` labels are exactly one of `-1`, `0`, or `1`; `kick` is exactly `0` or `1`. Corrupt labels are rejected instead of rounded, clipped, or thresholded into plausible targets.
7. Every train and holdout shard must expose the exact same ordered input-column layout used to compute normalization and initialize model weights; equal-width reordered features fail closed.
8. Failure happens before `model.npz` or `metrics.json` publication.

Valid current shard layouts and baseline training behavior are unchanged.

## Scope

This contract hardens only the BC baseline consumer and focused baseline tests. It does not change the shard producer, shard/bundle/training-chain auditors, runtime, ingestion, evaluation, champion/promotion state, or live services.

Tracked by #440.
