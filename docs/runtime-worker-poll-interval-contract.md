# Runtime worker polling interval contract

The `haxlab-worker` idle polling interval is a CLI timing boundary for the always-on data pipeline.

## Contract

`--interval` is validated during argument parsing, before `RuntimeState` is opened.

Accepted values:

- are supplied as canonical CLI text without surrounding whitespace;
- parse to a finite number;
- are greater than or equal to 1 second;
- preserve the requested finite value exactly after numeric parsing.

The default remains 5 seconds.

Rejected values include:

- zero and negative intervals;
- positive values below 1 second;
- `NaN`, positive or negative infinity, and values that overflow to infinity;
- non-numeric text and surrounding-whitespace variants.

Invalid CLI values use normal `argparse` failure semantics. They do not reach the state database or the worker loop.

## Runtime semantics

When the worker finds no pending replay, it sleeps for the already-validated interval. There is no runtime clamp such as `max(1.0, interval)`; malformed timing cannot be silently normalized.

When work is pending, existing behavior is unchanged: the worker continues draining batches without adding an idle sleep between non-empty batches.

## Non-scope

This contract does not change:

- replay probing or decompression;
- batch-size validation;
- runtime queue ordering or retry semantics;
- archive/scanner/analyzer/daemon behavior;
- ingestion/M0 artifacts;
- evaluation or champion state.
