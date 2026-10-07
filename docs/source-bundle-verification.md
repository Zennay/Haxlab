# Persisted source-bundle verification

The source-bundle receipt identifies the exact HBR2 and Discord JSON bytes that HaxLab's M0 importer can consume. `haxlab.ingestion.source_bundle_verify` verifies a persisted expected receipt against the current immutable export before downstream work trusts that source identity.

This is a read-only verification layer. It does not publish M0 artifacts, mutate raw exports, or authorize model promotion.

## Verification contract

The verifier accepts:

1. an immutable export root;
2. a persisted `haxlab-source-bundle-receipt-v1` JSON file.

It validates the expected receipt before touching current source evidence. The expected receipt must have exactly the canonical schema fields, native non-negative integer counts/sizes, canonical lowercase SHA-256 values, safe root-relative POSIX paths, importer-compatible source kinds, unique paths, canonical file ordering, internally consistent aggregate counts and a correct self-digest.

JSON parsing is strict:

- duplicate object keys are rejected;
- `NaN`, `Infinity` and `-Infinity` are rejected;
- coercible values such as booleans/strings are not accepted as integer evidence;
- unknown fields are rejected instead of silently ignored.

## Filesystem safety

The expected receipt file must be a stable regular file reached through one immutable path. The verifier rejects:

- platforms without directory + no-follow descriptor support;
- missing/unreadable receipt files;
- symlinks or non-directory components anywhere in the receipt parent chain;
- receipt symlinks and non-regular files;
- parent-directory or final-file device/inode replacement;
- size/mtime/ctime changes during or immediately after reading;
- receipts larger than the bounded parser limit.

The verifier opens the absolute receipt parent one component at a time relative to already-bound directory descriptors, then opens the final receipt through that bound parent with no-follow semantics. After every descriptor-backed receipt read it immediately reopens the configured logical path and requires that path to resolve back to the same parent-directory identities and the same receipt device/inode/size/mtime/ctime signature. After validating the expected receipt, it recomputes current source identity through `create_source_bundle_receipt()`, repeats the bound read, requires the complete binding to match the first read, and finally requires the receipt bytes to be unchanged. Replacing the path with an identical-byte copy or mutating the same inode after the second descriptor read therefore still fails closed.

The source tree itself inherits all fail-closed source receipt protections, including relevant-file hashing, symlink rejection and inventory-drift detection.

## Result

A structurally valid comparison emits `haxlab-source-bundle-verification-v1` with:

- `clean`;
- expected and current receipt SHA-256 values;
- missing expected source paths;
- unexpected current source paths;
- changed source paths whose kind, size or digest differs.

The result is deterministic and contains no timestamps or absolute paths.

## CLI

```bash
python -m haxlab.ingestion.source_bundle_verify \
  /path/to/export \
  /path/to/source-receipt.json
```

Exit codes:

- `0`: expected receipt exactly matches current source evidence;
- `1`: both inputs are structurally trustworthy, but source drift exists;
- `2`: the expected receipt or current source evidence is unsafe/malformed and cannot be trusted.

All outputs are compact machine-readable JSON.

## Integration boundary

This verifier remains source-side provenance only. It does not:

- edit ingestion discovery, Discord parsing, matching or M0 publication;
- bind a source receipt into dataset-generation metadata;
- change runtime scanner/archive/state/analyzer behavior;
- change learning manifests/shards;
- change evaluation gates, thresholds, models or champion pointers.

Any future source→M0 chain binding should consume this verified evidence rather than weakening these contracts.
