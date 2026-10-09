# Raw archive audit ledger-evidence contract

The raw archive auditor verifies persisted `raw_replays` rows against immutable content-addressed HBR2 files. Because SQLite uses dynamic typing, the auditor must validate persisted row values before they influence path construction, expected sizes, or filesystem reads.

## Contract

For every `raw_replays` row:

1. `sha256` is a native string containing exactly 64 lowercase hexadecimal characters.
2. `archive_path` is a native, non-empty string.
3. `size_bytes` is a native non-negative integer.
4. A malformed row fails closed as `invalid_ledger_evidence:<field>` before any archive member is opened.
5. Malformed SQLite scalars are rendered into JSON-safe diagnostic strings; they are never coerced into trusted evidence.
6. Existing content-address path, stable-inode, size, hash, symlink, and parent-directory checks remain unchanged for valid rows.

Malformed ledger evidence contributes to the existing `read_failures` and `objects_with_issues` counters so the v1 audit receipt shape remains stable.

## Scope

This contract changes only the raw archive audit consumer. It does not modify the runtime SQLite schema, archive producer, scanner, worker, analyzer, ingestion, learning, evaluation, champion/model state, or live services.

## Regression evidence

`tests/test_archive_audit.py` proves that uppercase/noncanonical digests, empty archive paths, non-integer sizes, and BLOB digest values are rejected before filesystem reads. It also proves the CLI emits deterministic JSON instead of raising on malformed persisted size evidence.

Tracked by #437.
