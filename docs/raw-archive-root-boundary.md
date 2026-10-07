# Raw archive root boundary

HaxLab's raw archive writer already verifies source bytes from stable file descriptors and publishes immutable replay objects without clobbering an existing destination. The directory path that contains those files is part of the same integrity boundary.

`archive_replay()` now opens the configured `raw_root` one pathname component at a time with directory + no-follow semantics. Missing components may be created only beneath an already-open parent descriptor and are immediately reopened with the same no-follow contract. Existing symlinks or non-directory components fail closed.

The same rule applies to:

- `.staging`;
- the first two SHA-256 prefix directories;
- all staging, duplicate-verification and publication I/O below those descriptors.

For file operations, the implementation uses `/proc/self/fd/<directory-fd>/...` paths on the Linux HaxLab runtime. Those paths remain bound to the opened directory inode even if an outside pathname is renamed while an ingest is active. Before a new ledger row is committed—and again before returning—the configured logical `raw_root` is reopened through the no-follow chain and must still identify the original root inode.

The persisted `raw_replays.archive_path` and `ArchiveResult.archive_path` remain normal paths under the configured `raw_root`; descriptor paths are execution-only and are never stored as provenance.

A violation fails before a new raw ledger row is committed. Existing source-inode, destination hash/no-clobber and duplicate verification rules remain unchanged.
