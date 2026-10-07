# Runtime queue limit contract

HaxLab runtime workers read pending replay work through
`RuntimeState.list_unprocessed_replays()` and
`RuntimeState.list_unanalyzed_replays()`.

Their `limit` argument is an execution-control boundary, not a value to coerce.
Both readers therefore require an exact native Python `int` greater than zero.

Rejected values include booleans, floats, strings, zero, negative integers and
`None`. Rejection happens before SQL execution and raises `ValueError`.

This keeps batch sizing explicit and deterministic while preserving the existing
queue ordering:

- raw replay insertion time, then SHA-256;
- retryable analysis rows remain eligible;
- successful current-version analysis rows remain excluded.

This contract is intentionally narrow. It does not alter runtime-state writes,
event retention, ledger auditing, backup/restore behavior, ingestion, analysis,
evaluation or champion state.
