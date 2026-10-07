# Player-stats input integrity

`haxlab.runtime.player_stats.collect()` aggregates analyzer-produced JSON only after the complete artifact passes a narrow runtime contract.

## Accepted artifact shape

- the JSON root is a plain object;
- `schemaVersion` is a native integer equal to 3 or 4;
- `players` is a list;
- every player entry is a plain object;
- `name` and `authHash`, when present, are strings;
- count metrics are native non-negative integers;
- `touchProgressionSum` is a native finite integer or float;
- nearest-ball and close-ball sample counts cannot exceed total samples.

Missing optional counters keep their historical zero default.

## Fail-closed behavior

Validation completes for every player before any row from that artifact is added to the cross-match totals. If one player entry is malformed, the entire artifact is ignored. This prevents a corrupt later entry from leaving a partially aggregated earlier player behind.

The contract deliberately does not change player ranking, estimator weights, match quality policy, replay matching, or evaluation/promotion state.
