# Human-imitation training manifest audit

`haxlab.learning.manifest_audit` independently verifies an already-published `haxlab-human-imitation-manifest-v3` artifact. It is intentionally read-only and does not share producer code with `learning/selector.py`.

## Why this boundary exists

The manifest is the control artifact between analyzed replays and imitation-shard extraction. It records selected players, source provenance, quality counts, and deterministic train/holdout assignment. Producer success proves those checks ran once; it does not prove the artifact remained coherent afterwards.

The auditor therefore fails closed when published bytes no longer satisfy the v3 contract.

## Verified invariants

The audit checks:

- bounded no-follow/non-blocking regular-file reads, including repeated reads from the same descriptor;
- stable file identity and exact byte count during verification;
- UTF-8 JSON with unique object keys and no NaN/Infinity constants;
- exact top-level, selection, stats, selected-player and replay-entry field sets;
- native finite thresholds/counts and valid holdout modulus/bucket bounds;
- unique selected-player identities and producer-compatible ordering by conservative score, rating, then matches;
- exact stats versus selected/train/holdout list counts;
- canonical replay SHA-256 identities and strict train/holdout disjointness/order;
- deterministic `haxlab-holdout-v1` bucket recomputation for every replay;
- canonical raw replay provenance path from `raw_root`;
- analysis filename/root provenance, SHA/size evidence, frame-to-duration consistency;
- selected replay-player IDs, identities, sample counts and accepted quality evidence.

No source artifact is rewritten or repaired.

## Receipt

Success emits compact JSON with schema `haxlab-human-imitation-manifest-audit-v1`, exact manifest byte size/SHA-256, analysis version, selected/train/holdout counts and a deterministic `inventory_sha256` over semantic manifest content excluding only `generated_at`.

## CLI

```bash
python -m haxlab.learning.manifest_audit \
  /var/lib/haxlab/derived/training/human-imitation-state-pass-v4.json
```

Success exits 0. Contract failures exit 2 and emit machine-readable failure JSON on stderr.

## Ownership separation

This module does not modify `learning/selector.py`, `learning/shards.py`, skill generation/auditing, runtime state/finalization, ingestion, evaluation or champion state. Producer-side manifest count/provenance work remains independently owned.
