# Runtime execution-limit contract

HaxLab runtime queue consumers fail closed when execution limits are invalid.

## Queue batch size

`RuntimeState.list_unprocessed_replays()` and
`RuntimeState.list_unanalyzed_replays()` own the canonical queue-limit rule:
the limit must be a native Python `int` greater than zero.

Runtime entrypoints must pass the configured batch size through unchanged.
They must not clamp zero or negative values with `max(1, ...)`, because that
turns a configuration error into real processing work.

This applies to both:

- `haxlab-worker --batch-size`;
- `haxlab-analyzer --batch-size`.

Direct `analyze_batch()` callers inherit the same RuntimeState validation, so
bools, floats, strings, `None`, zero and negative limits are rejected instead
of being coerced.

## Analyzer concurrency

`analyze_batch(..., workers=...)` requires a native positive integer worker
count. Validation happens before reading the queue, including when the queue is
empty. Invalid concurrency configuration therefore cannot remain latent until
work later appears.

The CLI passes `--workers` through unchanged to this validation boundary and
does not silently replace invalid values with one worker.

## Deliberate non-scope

This contract does not change replay selection ordering, retry semantics,
SQLite schema, analyzer sampling cadence, decoder timeout policy, ingestion,
archive behavior, M0 publication, evaluation, or champion state.
