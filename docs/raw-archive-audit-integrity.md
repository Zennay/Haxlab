# Raw archive audit parent-directory integrity

The raw replay archive is content-addressed, but an audit must prove more than byte equality. A replay read is only valid when the logical content-address directory remains the same directory object for the complete snapshot.

## Contract

For every ledger-backed replay audit:

1. The immediate content-address parent directory is opened with `O_DIRECTORY | O_NOFOLLOW`.
2. The replay member is stat'ed and opened relative to that held directory descriptor.
3. The opened replay must remain the same regular-file identity while it is hashed.
4. The member name must still resolve to that same file identity inside the held directory before success.
5. The logical parent path must still resolve to the exact held directory `st_dev/st_ino` before success.

The audit fails closed when the parent is missing, symlinked, replaced or rebound. A byte-identical replacement is still rejected because archive evidence includes stable object identity, not only content.

## Scope

This contract hardens only `haxlab.runtime.archive_audit`. It does not change archive publication, runtime ledger schema, analyzer behavior, ingestion, learning, evaluation or champion state.

## Regression evidence

`tests/test_archive_audit.py` covers:

- healthy ledger/archive verification;
- file replacement before open;
- file mutation during hashing;
- file symlink replacement;
- byte-identical parent-directory replacement while the held descriptor still points to the old directory;
- a pre-existing symlinked content-address parent;
- CLI fail-closed reporting/truncation.

Issue: #370.
