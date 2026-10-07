# Archive source inode-snapshot contract

`haxlab.runtime.archive.archive_replay()` is the final trust boundary between an incoming replay pathname and the immutable content-addressed raw archive. The bytes accepted into the archive must come from the same regular-file inode whose source identity was checked.

## Descriptor-bound source copy

The archive producer now:

1. captures the source path with `lstat()` and rejects symlink or non-regular evidence;
2. opens the leaf with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (plus `O_CLOEXEC` where available);
3. binds the opened descriptor's device/inode identity to the initial pathname identity;
4. copies and hashes only bytes read from that verified descriptor;
5. requires the descriptor's device, inode, size, mtime and ctime to remain unchanged across the copy;
6. re-checks the pathname before accepting the private staging snapshot;
7. retains the existing outer pre/post source signature guard so a change after the copy helper returns but before publication also fails closed.

Any rejected source leaves no published HBR2 object and no `raw_replays` registration. Private staging files are removed on failure.

## Preserved behavior

Healthy sources retain their existing content-addressed destination layout and SHA-256 identity. Duplicate content remains deduplicated to the existing raw object. Replay validation still runs against the private staging snapshot before publication.

## Scope

This changes only the archive producer's final source-copy boundary. Scanner discovery/timing is owned separately by #97. Published raw evidence verification is owned by the completed raw-archive audit lane (#194/#196). RuntimeState/schema, analyzer, ingestion M0, learning, evaluation, models and champion state are unchanged.
