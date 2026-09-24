# System Architecture

## Pipeline

```text
Discord JSON + HBR2
        |
        v
Raw archive + content hashes
        |
        v
Ingestion / deduplication
       / \
      v   v
report parser   replay parser
      \   /
       v v
canonical match model
        |
        v
feature engine
   /         \
  v           v
quality      analytics
  |             |
  v             v
skill model   AI coach
  |
  v
dataset curriculum
  |
  v
imitation learning
  |
  v
self-play / RL
  |
  v
evaluation arena
  |
  v
champion registry
```

## Module boundaries

- `ingestion` — sources, hashes, dedupe, report/replay matching.
- `replay` — HBR2 decoding and canonical frame/event extraction.
- `features` — deterministic derived features.
- `quality` — data validity and weighting.
- `skill` — identity, role-aware player estimates and uncertainty.
- `analysis` — match/player/team aggregation.
- `coaching` — evidence-backed recommendations.
- `learning` — datasets, imitation and self-play/RL.
- `evaluation` — benchmarks, regressions and champion promotion.

## Provenance

Every derived artifact should be traceable to:

- replay SHA-256;
- Discord message ID when available;
- parser version;
- feature version;
- dataset version;
- code commit;
- configuration.

Raw inputs are immutable.

## Storage

Start local-first:

- raw JSON and HBR2 files on disk;
- Parquet/DuckDB or SQLite for canonical/feature data;
- versioned model artifacts;
- explicit manifests for datasets.

Avoid infrastructure complexity until real volume requires it.
