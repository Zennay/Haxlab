# Source-file ledger status write contract

The `source_files` table is the ingestion-side ledger for discovered replay inputs. Scanner producers currently write exactly three terminal source statuses:

- `archived`: a newly archived raw replay;
- `duplicate`: source bytes already existed in the content-addressed raw archive;
- `failed`: ingestion could not safely archive the source.

`RuntimeState.mark_seen()` is the write boundary for this evidence.

## Fail-closed rule

Only native Python `str` values exactly equal to `archived`, `duplicate`, or `failed` may reach the source ledger INSERT/UPSERT.

The boundary rejects, before executing SQL:

- unknown statuses such as `ok`, `retry`, or arbitrary labels;
- case/whitespace variants;
- empty strings;
- booleans and integers that SQLite could otherwise coerce;
- bytes and other non-string values;
- subclasses of `str`.

Rejected first writes create no source row. Rejected updates cannot overwrite size, mtime, SHA-256, status, or error evidence from an existing valid row.

## Existing semantics preserved

Canonical status updates retain the current upsert behavior. A source may therefore move between canonical scanner-produced states when the scanner legitimately observes new evidence for the same source path.

`status_snapshot()` continues to derive:

- `source_archived` from canonical `archived` rows;
- `source_duplicates` from canonical `duplicate` rows;
- `source_failed` and ingest-integrity state from canonical `failed` rows.

Preventing unknown values at the write boundary keeps those counts aligned with runtime-ledger audit expectations.

## Ownership boundary

This contract changes only the source-status validation inside `RuntimeState.mark_seen()`. It does not alter:

- scanner/archive behavior;
- raw replay registration;
- processing or analysis status contracts;
- runtime-ledger auditing;
- worker polling or autonomy status;
- M0 ingestion artifacts;
- evaluation, model, threshold, or champion state.

Integration requires exact-head HaxLab CI and a fresh main/ownership recheck.
