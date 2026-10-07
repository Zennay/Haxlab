# Training chain audit

`haxlab.learning.training_chain_audit` verifies that a structurally valid training
manifest and a structurally valid train/holdout shard bundle still describe the
same published training evidence.

## Contract

The audit is read-only and fail-closed. It first requires the existing training
manifest audit and train/holdout shard-bundle audit to pass. It then binds both
shard indexes to the exact audited manifest path, manifest schema and
`analysis_version`.

For each split, the complete requested replay inventory — successful shards plus
failed extraction requests — must equal the corresponding manifest replay set
exactly. Missing, added or split-reassigned replay identities fail the audit.

For every successful shard request, the index must copy the manifest's
`raw_path`, `selected_player_ids`, `selected_players` and
`example_weight` evidence exactly. Failed requests remain valid inventory
evidence, but their replay identity must still belong to the correct manifest
split.

The manifest and both shard indexes are re-read after cross-artifact validation.
Any byte change during the chain audit fails closed.

## Receipt

A clean receipt records:

- exact manifest SHA-256 and the manifest auditor inventory digest;
- the shard-bundle inventory digest;
- train and holdout replay counts;
- a deterministic cross-artifact inventory SHA-256 over split, replay identity,
  success/failure status and copied training evidence;
- one deterministic `chain_sha256` root digest over the exact manifest SHA-256,
  manifest-audit inventory digest, shard-bundle inventory digest and
  cross-artifact inventory digest.

Changing any bound component therefore changes the root receipt even when the
other publication evidence remains byte-identical.

This receipt proves publication-chain consistency only. It does not modify the
selector, manifest producer, shard producer, model, evaluation policy, runtime
state or champion pointer.

## Scope

This implements GitHub issue #163 as a standalone post-publication verifier. It
reuses the integrated manifest and shard-bundle auditors and intentionally leaves
their owned implementations unchanged.
