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

The expected receipt file must be a stable regular file. The verifier rejects:

- missing/unreadable receipt files;
- receipt symlinks and non-regular files;
- descriptor/path identity changes;
- size/mtime/ctime changes during or immediately after reading;
- receipts larger than the bounded parser limit.

After validating the expected receipt, the verifier recomputes current source identity through `create_source_bundle_receipt()`. It then reads the expected receipt again and fails if its bytes changed during current-source hashing.

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
