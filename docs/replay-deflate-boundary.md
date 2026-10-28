# Replay DEFLATE boundary

HBR2 v3 stores one raw-DEFLATE member after the 12-byte replay header. `decompress_replay_payload()` treats that member as the complete payload boundary.

## Contract

- the HBR2 magic and supported version are validated before decompression;
- malformed raw-DEFLATE bytes fail as `deflate_error`;
- a stream that never reaches the DEFLATE end marker fails as `deflate_incomplete`;
- bytes remaining after the DEFLATE member fail as `deflate_trailing_data`;
- valid v3 payloads return the same decompressed bytes as before.

The helper does not introduce a replay-size policy and does not modify basic replay validation, discovery, ingestion, runtime processing, learning, evaluation or promotion state.

## Replay file identity admission (#453)

Both the 12-byte header reader and raw-DEFLATE payload reader now open through a single stable regular-file descriptor. A symlink at the final replay entry is rejected before content is trusted; FIFOs, sockets, devices and directories are not valid replay files. An opened file must still match the lexical path's pre-open device/inode identity, otherwise ingestion fails closed on a path-replacement race. Systems without `O_NOFOLLOW` support explicitly reject admission.

This check protects the **final replay entry**; it does not attest every ancestor directory against symlinks or bind files across separate calls. It does not add a payload size policy or change HBR2 magic/version/DEFLATE parsing behavior. Replay and model work remain subject to existing provenance and authorized data gates.

Ported existing product source from historical draft PR #454 onto fresh current-main without copying or creating QA files or touching the live host's replay archive, model, workers or production state. Existing exact-head CI and independent review are required before merge.
