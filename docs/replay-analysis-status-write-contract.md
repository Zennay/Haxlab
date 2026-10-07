# Replay analysis status write contract

`RuntimeState.mark_replay_analysis()` is the producer boundary for the
versioned replay-analysis ledger.

## Canonical statuses

The write API accepts exactly three native string statuses:

- `ok` — analysis completed successfully;
- `failed` — analysis completed with failure evidence;
- `retry` — the existing explicit control-plane state that makes a replay
  eligible for analysis again.

SQLite-coercible lookalikes and unknown strings are rejected before an INSERT
or UPSERT. This prevents malformed rows from silently removing a replay from
the analyzer queue.

## Failure semantics

A rejected first write creates no `replay_analysis_versions` row. A rejected
update does not overwrite a previously valid row.

The queue contract remains unchanged: a successfully probed replay is
analyzable when its current analyzer-version row is missing or has
`status='retry'`.

## Deliberate non-scope

This contract does not change probe-processing retry semantics, queue SQL,
analyzer output payloads, analysis metrics, event retention, runtime-ledger
audits, ingestion/M0, evaluation, or champion state.
