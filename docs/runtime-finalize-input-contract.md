# Runtime analysis finalizer input-integrity contract

The analysis finalizer publishes the versioned skill leaderboard, human-imitation
manifest, and analysis completion receipt only after the runtime ledger reports a
complete analysis snapshot.

## Native input boundary

`finalize_analysis_if_ready()` is fail-closed at its public boundary:

- `min_matches` must be a native positive integer. Booleans, floats, strings,
  zero, and negative values are rejected instead of coerced or clamped.
- `min_minutes` must be a native finite non-negative integer or float. Booleans,
  strings, negative values, NaN, and infinities are rejected.
- Runtime snapshot counters consumed by finalization must be native
  non-negative integers.

No artifact is published for invalid threshold inputs.

## Existing completion evidence

Existing `_complete.json` data is an optimization only; it is never authority
when its types drift. A prior completion may authorize `already_finalized`
only when:

- the JSON document is an object;
- `analysis_version` exactly matches the current analyzer version;
- `raw_unique_replays`, `analysis_ok`, and
  `analysis_ticks_reconstructed` are native integers and exactly match the
  live runtime snapshot;
- the existing training manifest is an object with the current manifest schema.

Malformed or coerced prior evidence is treated as stale and rebuilt.

## Leaderboard producer evidence

Every leaderboard row consumed by the finalizer must be an object with:

- `matches`: native non-negative integer;
- `minutes`: native finite non-negative integer or float.

Malformed rows fail closed before the finalizer publishes a new leaderboard.
This prevents producer drift such as `"20"`, `true`, NaN, or infinity from
silently changing eligibility.

## Scope

This contract covers runtime analysis finalization only. It does not change
queue selection, worker/analyzer entrypoints, replay scanning or archiving,
evaluation gates, model bytes, or champion state.


## Cache binding

`already_finalized` is authorized only when the existing leaderboard also
matches the current finalizer contract. The cached leaderboard must have the
expected schema/version/source root, exact `min_matches` and `min_minutes`,
a list of rows, and native bounded row evidence. Changing either threshold or
tampering with those fields forces a rebuild instead of reusing stale output.
