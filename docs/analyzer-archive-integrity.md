# Analyzer raw archive integrity

The replay probe validates raw archive evidence before processing, but analysis happens later. The analyzer therefore must not trust the archive pathname merely because the runtime ledger already contains a successful probe row.

Before starting the Node decoder, `haxlab.runtime.analyzer` now creates a private decode snapshot from one verified no-follow descriptor:

- the archive path must exist, be a regular file, and not be a symlink;
- `lstat -> open(O_NOFOLLOW) -> fstat` must refer to one stable device/inode;
- size, mtime and ctime must remain stable while the descriptor is read;
- the pathname must still resolve to the same regular-file identity after the read;
- byte count must equal `RawReplayRecord.size_bytes`;
- SHA-256 must equal `RawReplayRecord.sha256`;
- the exact verified bytes are copied into an fsynced temporary decode snapshot;
- the directory entry is unlinked before decoder launch while its file descriptor stays open;
- Node receives `/proc/self/fd/<fd>` through an explicitly inherited descriptor, never a reopenable archive/snapshot pathname;
- the descriptor is closed immediately after decoder execution.

If any raw-archive check fails, decoding is not started. The selected replay receives a failed current-version analysis row with an `archive_integrity_error:...` reason and no successful output artifact is published.

This boundary is intentionally later than worker verification. It protects the time gap between successful probe evidence and analysis execution while preserving the already-integrated orphan-analysis recovery rule: a pending/retry replay is recomputed and an orphan derived JSON file is never promoted by itself.
