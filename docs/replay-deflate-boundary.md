# Replay DEFLATE boundary

HBR2 v3 stores one raw-DEFLATE member after the 12-byte replay header. `decompress_replay_payload()` treats that member as the complete payload boundary.

## File identity contract

Both `read_replay_header()` and `decompress_replay_payload()` consume replay bytes only from a stable regular-file identity:

- the lexical replay path must name a regular file directly, not a symlink;
- non-regular filesystem objects are rejected before content is trusted;
- the file descriptor opened for reading must still match the device/inode observed for the lexical path immediately before open;
- where the host supports `O_NOFOLLOW`, the open itself also refuses a final-component symlink.

A pathname replacement race therefore fails closed as `unsafe_replay_path:identity_changed` instead of silently consuming bytes from a different replay.

## DEFLATE contract

- the HBR2 magic and supported version are validated before decompression;
- malformed raw-DEFLATE bytes fail as `deflate_error`;
- a stream that never reaches the DEFLATE end marker fails as `deflate_incomplete`;
- bytes remaining after the DEFLATE member fail as `deflate_trailing_data`;
- valid v3 payloads return the same decompressed bytes as before.

The helper does not introduce a replay-size policy and does not modify basic replay validation, discovery, ingestion, runtime processing, learning, evaluation or promotion state.
