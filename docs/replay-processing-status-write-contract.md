# Replay processing status write contract

`RuntimeState.mark_replay_processing()` is the producer boundary for the
runtime replay-probe ledger. Downstream analysis selects only processing rows
whose status is exactly `ok`, while the unprocessed queue selects only raw
replays without any processing row. A malformed status therefore cannot be
treated as harmless metadata: once persisted, it can strand a replay between
both queues.

## Canonical statuses

The write API accepts exactly two native string statuses:

- `ok` — replay probing completed successfully and the replay may become
  eligible for analysis;
- `failed` — replay probing completed with failure evidence and the replay
  remains recorded as processed but is not analysis-eligible.

Values that SQLite could otherwise coerce or store verbatim are rejected,
including booleans, numbers, bytes, empty strings, case variants, surrounding
whitespace, unknown strings, and `retry`. Processing currently has no retry
state; retry is an analysis-ledger concept only.

## Failure semantics

Status validation happens before the INSERT/UPSERT. A rejected first write
creates no `replay_processing` row. A rejected update cannot overwrite a
previously valid processing row or its parser/error evidence.

## Queue invariant

With canonical writes:

- a raw replay with no processing row is returned by
  `list_unprocessed_replays()`;
- a replay with processing status `ok` can be returned by
  `list_unanalyzed_replays()`;
- a replay with processing status `failed` is neither unprocessed nor
  analysis-eligible.

## Deliberate non-scope

This contract does not change replay probing behavior, retry policy, worker or
analyzer entrypoint limits, scanner/archive, finalization, runtime event
semantics, autonomy snapshots, M0 publication, evaluation, models, or champion
state.
