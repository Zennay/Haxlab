# Source bundle audit contract

The source-bundle receipt created by `haxlab.ingestion.source_bundle_receipt` is producer evidence. This auditor is a separate post-publication boundary: it treats that receipt as untrusted input and independently proves that the current importer-relevant source bytes still match it exactly.

## Command

```bash
python -m haxlab.ingestion.source_bundle_audit /path/to/export /path/to/source-receipt.json
```

Success returns exit code `0` and one compact JSON object with schema `haxlab-source-bundle-audit-v1` and `clean=true`. Any integrity failure returns exit code `2`, `clean=false`, and a stable error reason.

## Receipt validation

The auditor fails closed unless the receipt:

- is a stable, regular, non-symlink UTF-8 file;
- is strict JSON with no duplicate keys and no `NaN` / infinity constants;
- has exactly the v1 top-level and per-file fields;
- uses native non-negative integers rather than booleans/coerced values;
- uses canonical relative POSIX paths with no traversal;
- contains lowercase 64-character SHA-256 values;
- is in deterministic path order with no duplicate paths;
- has internally correct file/kind/byte counters;
- has a `receipt_sha256` equal to the canonical JSON digest of the unsigned receipt.

Unknown fields are rejected rather than ignored so a future schema cannot be silently interpreted as v1 evidence.

## Independent source scan

The auditor does not trust receipt counters or source identities. It independently walks the export root using the same importer-relevant boundary:

- `.hbr2` suffix is case-insensitive;
- Discord JSON uses lowercase `.json` only;
- unrelated files are ignored.

The export root, directories, and relevant source files must not be symlinks. Relevant files must be regular files. Every source file is opened without following symlinks where the platform supports `O_NOFOLLOW`; device/inode/size/mtime/ctime identity is checked before, during, and after the read. The full inventory is repeated after hashing so concurrent add/remove/rename drift fails closed.

## Exact equality requirement

The published receipt and the independent source scan must have exactly the same ordered:

- kind;
- root-relative path;
- byte size;
- SHA-256.

Adding, removing, renaming, replacing, truncating, or mutating any importer-relevant source invalidates the audit. Changes to unrelated files do not.

## Scope boundary

This layer is read-only and additive. It does not change the importer, scanner, runtime ledger, archive publication, M0 datasets, learning shards, evaluation gates, models, or champion state. Its purpose is only to turn a previously published source receipt into independently re-verifiable evidence.
