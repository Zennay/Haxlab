# Raw replay archive — crash-durable publication

The HaxLab raw replay archive uses no-clobber hard links to store immutable `.hbr2` bytes by SHA-256 and records their identities in a SQLite runtime ledger. Durably syncing the staged replay file **does not** by itself guarantee that newly-created directory names or the final hard-link directory entry survive a sudden power failure.

## New publication ordering

1. The existing source FD/read/hash integrity check and staged-file `fsync` remain unchanged.
2. Whenever raw-root ancestry, the `.staging` directory or either SHA prefix directory is created, sync the opened child directory and its opened parent directory in descriptor order. An unsupported directory fsync or an I/O failure raises a deterministic `raw_archive_root_sync_failed` / `raw_archive_directory_sync_failed:<component>` error before publication proceeds.
3. Publish without overwriting existing objects using the existing `os.link(..., follow_symlinks=False)` boundary; verify the expected destination SHA and path/inode identity as before. If another worker wins the link race, verify that winner instead.
4. Fsync the **opened SHA destination directory descriptor**, including the concurrent-winner case, before `RuntimeState.register_raw()` may commit a new ledger row. A failure is `raw_archive_destination_sync_failed` and no **new** ledger record is committed by this call.
5. Preserve the existing logical raw-root identity check and SHA/path ledger contract.

A crash after hard-link publication but before the directory sync/ledger commit may leave a recoverable orphan, not a falsely confirmed raw ledger row. A crash after the successful sync and ledger commit has a durable destination directory entry, subject to underlying filesystem guarantees and the separately governed SQLite durability policy.

This source-only product change does not rewrite, delete or inspect live archive files. It does not alter archive naming, replay validation, worker/learner/evaluation behavior, models or champion state. There are no QA files or manual tests in this PR. Existing exact-head automatic CI, independent review, and authorization gates remain mandatory before merge/deploy. Tracked by HaxLab #246.


## 21 October · Preserve both logical SHA-prefix path identities

The ledger persists a **logical archive path**
`raw_root/sha[0:2]/sha[2:4]/<sha>.hbr2`, but replay publication and SHA
verification intentionally operate through already-open directory FDs
under `/proc/self/fd/`. A stable root directory by itself is
insufficient evidence that its **two SHA-prefix child names** still
refer to the same bound descriptors. A concurrent pathname replacement
could otherwise let the ledger point to a different directory despite
successful publication and fsync on the original directory.

The pre-ledger boundary now reopens both child components from the
descriptor-bound archive root using `O_DIRECTORY|O_NOFOLLOW` and compares
their device/inode identities to the retained prefix FDs. Either a
missing/replaced first or second prefix fails closed as
`raw_archive_prefix_changed`, before a new ledger row is written.
The same check also protects the verified duplicate-return path.

The check does **not** claim atomic protection against a hostile actor
replacing components *after* the final inspection; directory ownership,
permissions and the downstream archive integrity scanner remain
important. No existing replay/archive files, SQLite state, models,
workers, or deployment services were mutated by this source change.
