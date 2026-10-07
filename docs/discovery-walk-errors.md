# Replay discovery traversal failure contract

Replay discovery must never treat an unreadable or partially traversable export tree as a complete canonical inventory.

The discovery walk therefore fails closed when:

- `os.walk()` reports a traversal error through its `onerror` callback;
- metadata for a discovered child directory cannot be read;
- a child entry presented as a directory is no longer a real directory.

Symlink directories remain explicitly pruned, as established by the discovery symlink-boundary contract. Regular replay candidates continue to use the no-follow descriptor snapshot from that contract.

This follow-up prevents permission, I/O, or directory-race failures from silently omitting part of the raw export tree.
