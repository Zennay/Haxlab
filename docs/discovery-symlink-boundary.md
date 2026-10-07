# Replay discovery symlink boundary

Replay discovery is an input-trust boundary for the immutable raw export tree. Canonical replay inventory must only contain regular replay files that are actually reachable inside that tree.

The discovery implementation now:

1. rejects a symlinked or non-directory discovery root;
2. walks the tree with symlink-directory traversal disabled and explicitly prunes linked directories;
3. rejects `.hbr2` symlink entries rather than following their targets;
4. opens each candidate with `O_NOFOLLOW`, binds pathname and descriptor identity, hashes the exact descriptor bytes, and verifies the file did not change during the read;
5. verifies the candidate resolves inside the canonical root before and after the snapshot;
6. builds `ReplayFile` size and SHA-256 evidence from that verified snapshot rather than reopening the path.

Case-insensitive `.hbr2` matching, deterministic path ordering, and content-hash deduplication are preserved.

This lane changes only replay discovery. Matcher, import artifact publication, source receipts, runtime scanner/archive, learning, evaluation and model state remain outside its ownership.
