# Basic replay payload-presence contract

`validate_replay_basic()` is the cheap HBR2 v3 admission check used before
deeper parsing/decompression. It is also consulted by ingestion inventory and by
the runtime archive before a staged replay can be published into the raw archive.

A replay is basic-valid only when:

- its 12-byte HBR2 header is readable and supported;
- `total_frames` is positive;
- at least one byte exists after the header.

A positive header with no payload fails closed as `truncated_payload`.
`invalid_total_frames` keeps precedence when both conditions are invalid so
existing diagnostic semantics stay stable.

This layer deliberately checks payload **presence**, not DEFLATE correctness.
Corrupt or incomplete compressed streams remain the responsibility of the
deeper replay probe/worker path. The contract therefore adds no duplicated
decompressor and no arbitrary replay-size limit.

## Non-scope

This change does not modify `replay/header.py`, scanner/source settlement,
producer parsing, worker archive-integrity checks, analyzer behavior, training
artifacts, evaluation, or champion state.
