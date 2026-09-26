# Analytics V1

Analytics V1 is a deterministic derived layer on top of the immutable
`state-pass-v4` replay-analysis outputs.

It does **not** mutate or replace state-pass-v4.

## Input contract

Supported decoder evidence is deliberately narrow:

- `schemaVersion == 4`
- `featureVersion == "touch-chain-v1"`

Unknown schemas fail closed. A new decoder feature version must be handled
explicitly instead of silently changing existing analytics semantics.

## Per-replay output

`haxlab.analysis.v1` derives compact match, player and team summaries from
existing replay evidence:

- team goals;
- ball summary and heatmap;
- player active time, mean position and heatmap;
- touches, kicks, self-retouches and teammate transfers;
- turnovers and recoveries;
- kick-transfer outcomes;
- pressure/retention rates;
- touch/kick progression;
- replay-inferred goal/assist evidence;
- team action totals and retention/transfer rates.

### Semantic limits

Current evidence does **not** justify calling every successful kick transfer a
semantic pass. It also does not expose a defensible complete shot-attempt
stream, and the per-player summaries are insufficient to reconstruct true
frame-level team spacing/shape.

Those metrics remain explicit limitations until a new versioned decoder feature
stores the required evidence.

## Batch builder

The batch CLI keeps analytics separate from replay analysis:

```bash
sudo -u haxlab haxlab-analytics \
  --source-root /var/lib/haxlab/derived/state-pass-v4 \
  --output-root /var/lib/haxlab/derived/analytics-v1
```

Each replay output records:

- replay SHA-256 identity;
- SHA-256 of the exact state-pass-v4 JSON bytes used as input;
- relative source path;
- decoder schema and feature version.

The output layout is content-addressed:

```text
analytics-v1/
├── manifest.json
├── 00/
│   └── <replay-sha256>.json
├── 01/
│   └── ...
└── ff/
    └── ...
```

## Incremental recomputation

If an existing analytics partition has the expected analytics schema, replay
identity and exact input-analysis SHA-256, the partition is reused.

If the source hash changes, the partition is rebuilt.

The manifest reports:

- scanned;
- succeeded;
- built;
- reused;
- failed;
- per-replay provenance;
- explicit failures.

This preserves the storage-at-scale rule that raw replay evidence is immutable
and only stale derived partitions are recomputed.

## Next evidence version

The next decoder feature version should add frame-derived team observations
needed for defensible:

- team width/depth;
- pairwise spacing;
- line/formation compactness;
- ball-relative team centroid;
- possession/transition team shape;
- explicit pass semantics;
- explicit shot-attempt semantics.

That must be written to a **new** derived analysis version rather than
retrofitted into state-pass-v4.
