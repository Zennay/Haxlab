# Immutable source bundle receipt

HaxLab's M0 importer consumes two source classes below one immutable export root:

- replay files whose suffix is case-insensitively `.hbr2`;
- Discord export files matched by the importer's lowercase `*.json` traversal.

`haxlab.ingestion.source_bundle_receipt` provides a read-only identity receipt for exactly that input surface. It does not replace M0 output receipts, semantic dataset audits, runtime archive evidence, or training-manifest/shard provenance.

## Receipt schema

A successful invocation emits `haxlab-source-bundle-receipt-v1` JSON containing:

- deterministic root-relative POSIX paths;
- source kind (`hbr2` or `discord_json`);
- byte size and SHA-256 for every importer-relevant file;
- per-kind counts and total bytes;
- one `receipt_sha256` over canonical compact JSON of the complete receipt body.

No absolute path, timestamp, inode, hostname, or creation time is included in the digest. Byte-identical source trees therefore produce the same receipt after relocation.

## Importer compatibility

The receipt intentionally mirrors current importer discovery semantics rather than inventing a broader file policy:

- `.hbr2` matching is case-insensitive, matching `discover_replays()`;
- Discord JSON matching is lowercase `.json`, matching `Path.rglob("*.json")`;
- unrelated files such as notes or other export clutter do not affect the receipt.

If importer discovery semantics change, this contract must change in the same integration sequence.

## Fail-closed boundaries

Receipt creation fails when source identity cannot be proven safely:

- missing, unreadable, symlinked, or non-directory export root;
- a symlink or non-directory anywhere in the export root's parent-component chain;
- any traversed symlink directory;
- any traversed directory whose device/inode changes after inventory;
- importer-relevant symlink or non-regular file;
- a file whose device/inode changes between discovery and open;
- a file whose size/mtime/ctime changes during the read;
- a file replaced or mutated immediately after the read;
- importer-relevant inventory or directory identity changes between the pre-hash and post-hash scans;
- the logical export-root path resolving to a different root inode before the receipt is returned.

The module opens the export root one path component at a time and keeps a bound directory descriptor for the complete operation. Nested traversal and relevant file opens are relative to already-bound directory descriptors with `O_NOFOLLOW` where the platform provides it. A final reopen of the configured logical root must resolve to the same device/inode before a receipt is emitted. The module never mutates the source tree.

## CLI

```bash
python -m haxlab.ingestion.source_bundle_receipt /path/to/export
```

Success exits `0` and prints one compact JSON receipt. A provenance failure exits `2` and prints compact machine-readable JSON with `clean=false` and an error code.

## Integration boundary

This lane is source-side provenance only. It does not modify:

- `ingestion/pipeline.py`, replay discovery, Discord parsing or matching;
- M0 dataset publication, generation, dataset receipt or semantic audit;
- runtime scanner/archive/state/analyzer paths;
- learning selectors, manifests or shards;
- evaluation, model, threshold or champion state.

It is intentionally separate from raw-archive destination/root publication hardening: this contract protects immutable import-source provenance, not the runtime content-addressed archive destination.

Integration requires exact-head repository CI and a fresh ownership/main-drift check.
