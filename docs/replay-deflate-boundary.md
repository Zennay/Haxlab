# Replay DEFLATE boundary

HBR2 v3 stores one raw-DEFLATE member after the 12-byte replay header. `decompress_replay_payload()` treats that member as the complete payload boundary.

## Contract

- the HBR2 magic and supported version are validated before decompression;
- malformed raw-DEFLATE bytes fail as `deflate_error`;
- a stream that never reaches the DEFLATE end marker fails as `deflate_incomplete`;
- bytes remaining after the DEFLATE member fail as `deflate_trailing_data`;
- valid v3 payloads return the same decompressed bytes as before.

The helper does not introduce a replay-size policy and does not modify basic replay validation, discovery, ingestion, runtime processing, learning, evaluation or promotion state.
