# Analysis finalizer manifest-provenance contract

The runtime finalizer publishes a leaderboard, a human-imitation training manifest, and an analysis completion marker. The `already_finalized` fast path is allowed only when those artifacts still belong to the same current analysis generation.

## Reuse invariant

Before returning `already_finalized`, `finalize_analysis_if_ready()` must prove all of the following:

- the completion marker still matches the current runtime snapshot;
- the leaderboard still matches the current analyzer version, analysis root, and threshold contract;
- the training manifest uses the current schema and analyzer version;
- manifest `analysis_root`, `raw_root`, and `leaderboard_path` match the expected current paths;
- manifest `leaderboard_sha256` and `leaderboard_size_bytes` match the current leaderboard bytes;
- manifest training counts are native non-negative integers;
- completion leaderboard/training counts match the currently loaded artifacts;
- completion stores and matches SHA-256 + byte size for both leaderboard and training manifest.

Any stale, malformed, swapped, or modified artifact makes reuse ineligible and forces a deterministic rebuild.

## Compatibility

Existing `haxlab-analysis-completion-v1` files created before this contract do not contain artifact digests. They are treated as stale once, rebuilt, and republished with the additional evidence fields. The schema identifier remains v1 because the new fields strengthen reuse validation without changing the consumer-visible completion meaning.

The valid unchanged fast path remains idempotent.

## Boundary

This lane modifies only `runtime/finalize.py`, its focused regressions, this document, and its branch-scoped proof workflow. It does not change analyzer execution (#231), RuntimeState schema, scanner/archive behavior, shard publication (#163), evaluation, models, or champion state.

## Proof

The exact-head proof runs the finalizer regressions plus the training selector and both training-manifest audit suites. Normal HaxLab CI must also pass on the same final branch SHA before integration.
