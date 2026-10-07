# Training manifest linked-source audit

`haxlab.learning.manifest_source_audit` verifies that a structurally valid human-imitation manifest still agrees with the source artifacts it names.

This is deliberately a second boundary after `manifest_audit`: internal manifest consistency and external source consistency are different failure classes.

## Verification chain

The source audit first requires the merged structural manifest audit to pass. It then:

- securely reopens the linked leaderboard, requires the manifest's exact recorded byte size and SHA-256, and runs the independent leaderboard audit;
- requires leaderboard `analysis_version` and `source_root` to match the manifest;
- independently replays the selector policy from audited leaderboard rows and requires exact selected-player output/order;
- securely reopens every referenced analyzed replay and requires the manifest's exact analysis size and SHA-256;
- rechecks the accepted analysis quality floor: schema v4+, two-minute frame floor, sampled-state evidence, at least four player rows, and positive touch evidence;
- recomputes selected replay-player identity/sample evidence from the linked analysis payload and requires exact equality with the manifest;
- requires every referenced raw replay path to exist as a regular non-symlink file.

The v3 manifest does not record raw replay byte hashes, so raw validation is intentionally limited to path existence/type/size snapshot evidence. It does not claim raw-byte identity.

## Receipt

Success emits `haxlab-human-imitation-manifest-source-audit-v1` with:

- manifest SHA-256;
- linked leaderboard SHA-256;
- analysis/raw artifact counts;
- `source_inventory_sha256`, a deterministic digest over linked leaderboard identity, exact analysis identities, and raw existence/size evidence.

## CLI

```bash
python -m haxlab.learning.manifest_source_audit \
  /var/lib/haxlab/derived/training/human-imitation-state-pass-v4.json
```

Success exits 0. Any structural, linked-byte, selection, quality, replay-player, or raw-path violation exits 2 with machine-readable failure JSON on stderr.

## Ownership separation

This module only reads already-published artifacts. It does not edit leaderboard/selector/shard producers, runtime state/finalization, ingestion, evaluation, model weights or champion state.
