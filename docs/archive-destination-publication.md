# Raw archive destination publication contract

The raw archive is immutable and content-addressed. Final publication must never overwrite an object that another process has already published, and any existing destination must be verified without pathname re-open races.

## Existing object verification

Duplicate and crash-recovery paths now verify a destination through one no-follow regular-file descriptor:

1. `lstat()` rejects symlink/non-regular destinations;
2. `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` opens the leaf without following a replacement symlink;
3. lstat↔fstat device/inode identity is required to match;
4. SHA-256 and byte count are computed from the already-open descriptor;
5. descriptor size/mtime/ctime and final pathname identity must remain stable through the read;
6. the descriptor digest must equal the content-addressed SHA-256.

A missing registered duplicate remains `raw_archive_missing:<sha>`. Unsafe, replaced, symlinked or hash-invalid destination evidence fails as `raw_archive_hash_mismatch:<sha>`.

## No-clobber publication

A new private staging object is published with an atomic hard-link operation instead of `Path.replace()`. The staging file and final content-addressed destination live under the same raw root, so the link publishes the fsynced inode without copying or overwriting.

If the destination already exists, publication never replaces it. The existing winner is accepted only after the secure descriptor verification above proves it contains the expected content-addressed bytes. An invalid concurrent winner is left untouched and the replay is not registered.

## Scope

This is only the destination publication/verification boundary in `runtime/archive.py`. Source inode snapshot hardening is completed by #199/#202. Published-object auditing is completed by #194/#196. Scanner, RuntimeState/schema, analyzer, M0, learning, evaluation, models and champion state are unchanged.
