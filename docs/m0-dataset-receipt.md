# M0 dataset receipt contract

The M0 ingestion publication is a five-file logical dataset:

- `manifest.json`
- `replays.json`
- `duplicates.json`
- `reports.json`
- `matches.jsonl`

`haxlab.ingestion.dataset_receipt` binds that set to exact bytes without
modifying the producer. The receipt is intended for downstream provenance,
CI evidence and later publication-generation coordination.

## Contract

A receipt is valid only when all five required names exist directly inside
the supplied dataset root and each entry can be opened as a regular file
without following the final path component as a symlink. The dataset root
itself is also opened without following a symlink.

For every artifact the receipt records:

- canonical artifact name;
- exact byte size;
- SHA-256 of the bytes read from the opened file descriptor.

The top-level `receipt_sha256` is the SHA-256 of canonical compact JSON over
`schema`, `artifact_count` and the ordered artifact records. Absolute
paths, mtimes and inode numbers are deliberately excluded, so relocating an
unchanged publication produces the same receipt.

The schema is `haxlab-m0-dataset-receipt-v1`.

## Fail-closed behavior

Receipt creation fails when:

- the dataset root is missing, unreadable, not a directory or a symlink;
- `O_NOFOLLOW` / `O_DIRECTORY` support is unavailable;
- any required artifact is missing;
- any required artifact is a symlink, directory, device or otherwise not a
  regular readable file.

This receipt does not replace semantic cross-artifact validation. It provides
an orthogonal exact-byte identity that a semantic auditor or downstream
consumer can record and compare.

## Usage

```bash
python -m haxlab.ingestion.dataset_receipt /path/to/m0-output
```

Success prints deterministic JSON and exits 0. Unsafe or incomplete input
prints machine-readable failure JSON to stderr and exits 2.
