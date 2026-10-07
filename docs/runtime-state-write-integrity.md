# Runtime ledger write-evidence contract

The runtime SQLite ledger is a provenance boundary. Data written by the scanner,
archive worker, replay probe and analyzer must already have the native type and
range that downstream audits and status consumers assume.

## Canonical identities and text

Persisted replay SHA-256 values are canonical lowercase 64-character hex
digests. Required path/version/stage strings are native strings containing at
least one non-whitespace character. Their bytes are preserved verbatim; the
write boundary does not trim or otherwise normalize valid text. Optional error
text remains free-form text, including an empty string, but it may not be a
non-string scalar.

This contract validates evidence; it does not normalize it.

## Numeric evidence

Sizes, mtimes, frame/tick counts, decompressed-byte counts, sampled-state
counts, player counts and raw-event counts are native non-negative integers
within SQLite's signed 64-bit INTEGER range. Booleans, strings, floats,
negative values and larger Python integers are rejected before sqlite3 can
raise a binding overflow.

Processing duration is optional native numeric evidence. When present it must
be finite, non-negative and representable as a Python float. Accepted integer
durations are normalized to that float before SQLite binding so a large but
float-representable Python integer cannot leak a sqlite3 integer-overflow.
NaN, infinities, negative values, strings, booleans and float-unrepresentable
integer magnitudes fail closed before any SQLite mutation.

## Failure atomicity

Validation happens before `INSERT` or `UPSERT` execution. A malformed write
therefore cannot create a partial row or overwrite an existing valid row.

Status-enum semantics are unchanged:

- source writes: `archived`, `duplicate`, `failed`;
- processing writes: `ok`, `failed`;
- analysis writes: `ok`, `failed`, `retry`.

The contract intentionally does not change the runtime schema, queue
selection, analyzer version, replay-processing semantics or event taxonomy.
