# Autonomy status control-evidence contract

The VPS autonomy tick consumes a small subset of `haxlab-status` as control evidence.
That evidence decides whether deterministic downstream work may run, so it must not be
silently coerced.

## Required fields

The parser requires these counters as native JSON integers greater than or equal to zero:

- `raw_unique_replays`
- `processing_ok`
- `processing_pending`
- `analysis_pending`
- `processing_failed`
- `analysis_failed`
- `analysis_ok`

Booleans, strings, floats, nulls, missing fields, and negative values are invalid even
when Python could coerce them to integers.

`analysis_version` must be a native non-empty canonical token containing only ASCII
letters, digits, dot, underscore, and hyphen, with a maximum length of 128 characters.
This keeps the shell handoff unambiguous and prevents whitespace/control-character
injection into derived artifact paths.

The parser also rejects impossible cross-counter relationships:

- `processing_ok + processing_failed + processing_pending` may not exceed
  `raw_unique_replays`;
- `analysis_ok + analysis_failed + analysis_pending` may not exceed
  `processing_ok`.

These are upper-bound checks rather than equality requirements because valid snapshots
may contain raw replays that have not yet entered processing, or successfully processed
replays that have not yet entered analysis.

Additional status fields are intentionally tolerated so `haxlab-status` can evolve
without coupling the autonomy tick to its complete schema. The successful shell handoff
still emits exactly the same six fields; `raw_unique_replays` and `processing_ok` are
consumed only as capacity evidence before that handoff is authorized.

## Failure behavior

`python -m haxlab.runtime.autonomy_status` reads one JSON object from stdin. On
success it emits exactly six validated shell-safe fields. On invalid JSON or invalid
control evidence it exits 2 and emits a JSON error with
`error=invalid_status_snapshot` to stderr.

`deploy/haxlab-autonomy-tick.sh` converts that parse failure into durable autonomy
state:

- state: `FAILED_RETRYABLE`
- action: `invalid_status_snapshot`

The tick then exits without building a leaderboard, manifest, shards, model, or running
the generation executor. Malformed status evidence can therefore never authorize
downstream work.
