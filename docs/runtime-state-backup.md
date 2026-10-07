# Runtime state backup contract

HaxLab's always-on runtime state uses SQLite in WAL mode. A direct filesystem copy of only the main database file is not a valid backup primitive because recently committed pages may still live in the WAL.

`haxlab.runtime.state_backup` creates an independent point-in-time snapshot without mutating the live runtime ledger.

## Guarantees

- The source must be an existing regular file.
- The source path and every existing source path component must not be a symlink.
- The destination parent must already exist as a real directory and may not traverse symlink components.
- The destination must not already exist. Backups never overwrite an earlier snapshot.
- Source integrity is checked with SQLite `PRAGMA quick_check` before copying.
- The snapshot is produced with SQLite's online backup API, which copies one consistent committed database view: committed WAL rows are included while uncommitted writer state is excluded.
- Online backup runs in bounded page batches with a monotonic deadline. The default is 30 seconds; timeout fails closed and removes unpublished temp evidence rather than leaving a hung backup operation.
- The completed temp snapshot is checked again with `PRAGMA quick_check`.
- The unpublished temp snapshot is mode 0600 and fsynced before publication.
- Publication uses a same-directory hard link, giving atomic no-overwrite semantics. If a destination appears concurrently, the new backup fails closed rather than replacing it.
- The destination directory is fsynced after publication and after temporary-name cleanup. If durability cannot be proven, the just-published link is rolled back when it still refers to the owned snapshot inode.
- The source device/inode identity is checked before and after the backup so path replacement during the operation fails closed.
- After durable publication, the final destination inode is sealed mode 0400 through a no-follow file descriptor and fsynced. Restore preflight rejects snapshots whose write bits are later re-enabled.
- The final destination is then reopened with SQLite `mode=ro&immutable=1`; its exact bytes, page count and `quick_check` result must still match the receipt before the operation succeeds.
- Immutable verification does not create `-wal`, `-shm` or journal sidecars for the published snapshot.
- Receipt hashing opens the snapshot with no-follow semantics and binds the file descriptor to the expected device/inode before and after reading, so a path or symlink swap cannot redirect SHA-256 evidence.

The returned `haxlab-runtime-state-backup-v1` receipt contains only content evidence:

- `size_bytes`
- `sha256`
- `page_count`

Absolute host paths and timestamps are deliberately excluded from the receipt.

## Usage

```bash
python -m haxlab.runtime.state_backup \
  /var/lib/haxlab/state/runtime.sqlite \
  /var/lib/haxlab/backups/runtime-2026-10-07.sqlite \
  --max-seconds 30
```

Successful output is a single JSON object with `ok=true` and the receipt. Contract failures return exit code 2 with machine-readable `ok=false` evidence. `--max-seconds` must be a positive finite native number; non-finite, boolean, string-coerced or non-positive values are rejected by the Python contract, while the CLI parser accepts only finite/positive values after the same fail-closed validation.

For restore preflight in Python, parse external JSON with `parse_backup_receipt(payload)` first and then call `verify_runtime_state_backup(snapshot, receipt)`. Receipt parsing is exact: the payload must be a JSON object with only `schema`, `size_bytes`, `sha256`, and `page_count`; native numeric types are required and unknown/missing fields or coercible lookalikes fail closed. The verifier rejects symlinked/non-regular snapshots, hashes the file twice around immutable SQLite verification, runs `quick_check`, and requires the receipt page count to match. A byte or metadata mismatch fails closed.

Filesystem contract failures—including inability to create the private temp file, apply mode 0600, fsync, or atomically publish—are converted to `StateBackupError`. The command-line interface therefore keeps its machine-readable `ok=false` / exit-code-2 contract for expected filesystem failures rather than leaking a traceback.

## Restore boundary

Verification authorizes only the snapshot bytes, not a live restore. Replacing the active state database remains an explicit operational action and must happen with all HaxLab writers stopped and with a separately controlled rollback plan. The backup module itself never swaps the live database.

## Scope boundary

This module only snapshots and verifies the runtime SQLite ledger. It does not:

- prune `runtime_events` (issue #103);
- semantically audit ledger records (issue #102);
- modify `runtime/state.py`;
- copy raw HBR2 archives or derived analysis artifacts;
- authorize restoration or replace the live database.
