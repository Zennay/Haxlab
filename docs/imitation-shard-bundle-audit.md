# Train/holdout shard bundle audit

The per-directory shard auditor proves that one finalized imitation-shard directory is internally coherent. It cannot prove that two individually valid directories still form the **same leak-free train/holdout publication**. `haxlab.learning.shard_bundle_audit` adds that cross-split boundary without changing the shard producer.

Run it only after both split directories are finalized:

```bash
python -m haxlab.learning.shard_bundle_audit \
  --train-dir /var/lib/haxlab/derived/training/shards/state-pass-v4/train \
  --holdout-dir /var/lib/haxlab/derived/training/shards/state-pass-v4/holdout
```

A clean pair exits 0 and emits `haxlab-imitation-shard-bundle-audit-v1`. Any pair-integrity failure exits 1.

## Contract

The bundle audit first snapshots both `_index.json` files through the existing no-follow bounded reader, then requires both directories to pass `audit_shard_directory()` independently. It additionally fails closed when:

- the train index is not labeled `train` or the holdout index is not labeled `holdout`;
- schema, manifest schema, analysis version, manifest path, or extraction cadence differ between splits;
- a canonical replay SHA appears in both split inventories, regardless of whether either side recorded it as success or failure;
- a split contains duplicate replay identities or its requested count does not match its success/failure identity inventory;
- either index changes between the initial pair snapshot, the per-directory audits, and the final pair re-read;
- either underlying shard directory fails its own byte/schema/provenance audit.

The success receipt binds both exact index SHA-256 values, both per-directory shard inventory SHA-256 values, aggregate split counts and a deterministic cross-split replay inventory SHA-256.

## Why this is separate from the manifest audit

The training-manifest auditor proves that the **published manifest** assigns replay identities deterministically and disjointly. The shard-bundle auditor proves that the **downstream extracted artifacts** still preserve that separation. This catches post-manifest copy/misrouting mistakes without changing the selector or extractor.

## Scope boundary

This module is read-only. It does not modify `learning/shards.py`, `learning/shard_audit.py`, selector/manifest code, baseline training, runtime state, ingestion, evaluation, models or champion state.
