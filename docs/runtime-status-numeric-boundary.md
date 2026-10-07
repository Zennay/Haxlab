# Runtime status finite-numeric boundary

`haxlab-status` is a read-side data-pipeline consumer. Runtime status evidence must be validated before it is used to calculate progress or ETA fields.

## Contract

The duration and rolling-rate fields consumed by `haxlab.runtime.status`:

- must be exact native `int` or `float` values;
- must be representable as Python finite floats;
- must be non-negative;
- reject NaN, Infinity, negative values, strings, booleans, and integers whose magnitude cannot be represented as a float;
- fail through the existing deterministic `invalid_status_snapshot:<field>` boundary instead of leaking `OverflowError`.

Canonical valid snapshot/output semantics are unchanged.

## Scope

This hardens only `src/haxlab/runtime/status.py` and its focused consumer-integrity tests. It does not modify `runtime/autonomy_status.py`, the SQLite schema or write path, scanner/archive/worker/analyzer, ingestion, learning, evaluation, model/champion state, or live services.

Tracked by #439.
