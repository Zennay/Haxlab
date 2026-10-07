# Imitation shard post-publication audit

HaxLab treats generated imitation shards as derived evidence, not as trusted input. The shard producer can succeed and the files can still become stale, truncated, replaced, partially copied, or inconsistent with `_index.json` later. This audit is an independent read-only verification pass for a **finalized** `train/` or `holdout/` shard directory.

## Contract

Run:

```bash
python -m haxlab.learning.shard_audit \
  --shard-dir /var/lib/haxlab/derived/training/shards/state-pass-v4/train
```

A clean audit exits 0 and emits `haxlab-imitation-shard-audit-v1`. Any structural or integrity failure exits 1.

The verifier fails closed when:

- the shard root, index, metadata, or shard is missing, symlinked, non-regular, unreadable, or malformed;
- index schema, manifest schema, split, cadence, replay identity, native integer counters, or success/failure identity sets are invalid;
- a success entry does not have exactly the canonical `<replay_sha256>.meta.json` and `<replay_sha256>.f32.gz` pair;
- index and metadata drift on any producer-published field other than the cache-only index `status`;
- shard schema, Float32 column layout, source replay identity, output filename, selected-player maps, sample accounting, or reconstruction evidence is inconsistent;
- the gzip stream is corrupt/truncated or its decompressed byte length differs from `samples * rowWidth * 4`;
- actual compressed bytes differ from producer metadata;
- index aggregates differ from the audited artifacts;
- orphan shard/metadata files or abandoned `.f32.gz.tmp-*` outputs exist;
- `_index.json` changes while the audit is in progress.

The receipt includes the exact index SHA-256 and a deterministic inventory SHA-256 over each audited replay identity, compressed-content SHA-256, compressed byte size, uncompressed byte size, and sample count.

## Scope boundary

This verifier intentionally does **not** change `haxlab.learning.shards`, cache behavior, manifest selection, training, or model promotion. Producer hardening remains owned by the shard-output/cache-provenance lanes. This module independently verifies the bytes that already exist on disk.

Only audit a finalized shard directory. If the producer is still updating it, the audit is expected to fail rather than return a false green.
