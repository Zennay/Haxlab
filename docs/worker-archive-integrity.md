# Worker raw-archive integrity boundary

The replay worker must not write processing evidence for bytes that are different from the immutable raw object recorded in `raw_replays`.

For every pending replay, the worker now:

1. samples the archive path with `lstat` and rejects symlinks and non-regular files;
2. opens the path with no-follow descriptor semantics;
3. binds the descriptor device/inode identity to the initial pathname identity;
4. reads, hashes and buffers exactly the descriptor bytes used for the probe;
5. rejects descriptor metadata or pathname identity changes during that read;
6. requires the byte count and SHA-256 to match the ledger record;
7. parses the HBR2 header and decompresses the payload from that same verified byte snapshot.

Any integrity failure is recorded as `replay_probe_failed`; no successful processing metrics are published for the replay.

This boundary is separate from the archive producer and the raw archive auditor. The producer owns safe publication into the content-addressed archive; the auditor reconciles persisted archive evidence. The worker owns the final trust boundary immediately before replay processing.
