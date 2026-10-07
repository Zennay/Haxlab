# Autonomy status coherence

`haxlab-autonomy-tick.sh` consumes machine-readable output from `haxlab-status` through `haxlab.runtime.autonomy_status`.

The parser treats that output as control evidence, not as loosely typed display data. In addition to native non-negative integer checks, it now enforces the same count relationships as the status producer:

- `processing_ok + processing_failed + processing_pending <= raw_unique_replays`;
- `analysis_ok + analysis_failed + analysis_pending <= processing_ok`.

These are upper-bound checks rather than equality requirements because a valid snapshot may contain source rows that have not yet entered processing, or successful processing rows that have not yet entered analysis.

The shell interface remains unchanged: the parser emits only `processing_pending analysis_pending processing_failed analysis_failed analysis_ok analysis_version` for the autonomy tick. `raw_unique_replays` and `processing_ok` are required solely to prove that those control fields are coherent before downstream automation starts.

Malformed types, negative counters, impossible count relationships, and non-canonical analysis-version tokens fail closed with `invalid_status_snapshot`.