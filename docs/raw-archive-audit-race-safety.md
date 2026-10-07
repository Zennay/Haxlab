# Raw archive audit race-safety contract

`haxlab.runtime.archive_audit` verifies immutable content-addressed HBR2 evidence. The auditor must bind its type, size and SHA-256 checks to the same opened inode; pathname checks followed by a second pathname open are not sufficient because the archive entry can be replaced between those operations.

## Descriptor-bound read

For every `raw_replays.archive_path`, the auditor now:

1. captures an initial `lstat()` and rejects a symlink or non-regular entry;
2. opens the leaf with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (plus `O_CLOEXEC` where available);
3. compares the opened descriptor's device/inode identity with the initial path identity;
4. streams size and SHA-256 from that already-verified descriptor;
5. compares descriptor metadata before and after the read;
6. re-checks the pathname after the read and requires it to still resolve to the same regular-file identity.

A replacement before open therefore fails as `archive_identity_changed` instead of hashing the replacement. A symlink swap fails as `symlink_not_allowed`. A mutation or path replacement during the read fails closed rather than publishing a clean audit.

## Report compatibility

Existing counters and reasons remain intact for missing files, content-address path mismatches, symlinks, byte-size drift and SHA-256 drift. The report adds `read_failures` for descriptor/open/identity/metadata failures that are neither ordinary missing evidence nor a directly observed symlink.

The schema remains `haxlab-raw-archive-audit-v1`; the added counter is backward-compatible evidence and no RuntimeState schema or producer behavior changes.

## Scope

This hardening changes only the raw archive verifier and its tests. It does not modify the archive producer, scanner, RuntimeState schema, analyzer, M0 ingestion, learning data, evaluation, model or champion state.
