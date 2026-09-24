# Storage at Scale

A few thousand HBR2 files are small in raw form but can become large after frame expansion.

For roughly 6000 multi-minute matches, storing every player/ball state on every 60 Hz frame as verbose JSON would create unnecessary tens-of-gigabytes (or more) of derived data.

## Storage tiers

### Raw

Keep original HBR2 files unchanged in the content-addressed archive.

This is the permanent source of truth.

### Match index

One compact row per match:

- replay hash;
- duration/frames;
- source/report links;
- map;
- participants;
- scores;
- quality flags;
- parser version.

Use SQLite/DuckDB/Parquet.

### Events

Store sparse events separately:

- inputs/kicks;
- touches;
- possession changes;
- passes;
- shots;
- goals;
- turnovers;
- joins/leaves;
- tactical events.

Events are much smaller than full-frame state.

### State samples

Do not persist every 60 Hz state unless a use case needs it.

Use one or more:

- lower-rate analysis samples, e.g. 10–20 Hz;
- event-centered windows;
- possession chunks;
- training examples sampled from meaningful states;
- compressed columnar/binary arrays.

### ML datasets

Training datasets are derived artifacts, not the source of truth.

They should contain only fields required by the current model and be rebuildable from raw replays + parser/features.

## Formats

Prefer:

- SQLite for runtime job/state ledger;
- DuckDB/Parquet for analytics;
- NumPy/Zarr/Parquet-style arrays for ML samples if needed;
- JSON only for small manifests/configuration/debug samples.

Avoid huge JSON frame dumps.

## Incremental recomputation

Each derived artifact records parser/feature/dataset versions.

When code changes:

- old raw HBR2 never changes;
- only stale derived partitions are rebuilt;
- replay hashes make cache keys stable;
- future replay batches append naturally.

This is critical when the dataset grows beyond the initial ~6000 matches.
