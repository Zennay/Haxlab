# Replay worker bounded decompression contract

The runtime worker verifies each raw archive against its immutable ledger evidence
before probing HBR2. The probe only needs the header and decompressed byte count;
it does not consume the decompressed payload itself.

## Memory contract

Raw DEFLATE output is consumed through bounded 1 MiB output chunks.

- the full decompressed payload is never retained as one Python `bytes` object;
- the worker accumulates only the decompressed byte count;
- valid payloads keep the same HBR2 magic/version and DEFLATE acceptance semantics;
- there is no arbitrary decompressed-size ceiling, so valid large replays are not
  rejected merely because they expand substantially;
- truncated or corrupt DEFLATE streams still fail closed as `deflate_error:...`.

The archive producer/ledger trust boundary remains unchanged. The worker still
reads one verified archive snapshot, checks path/inode stability, ledger size and
SHA-256, and only then probes those exact verified bytes.

## Persistence

Successful replay processing persists the exact decompressed byte count in
`replay_processing.decompressed_bytes` and emits the same count in the
`replay_probe_ok` event. Failed decompression never records a successful probe.

## Non-scope

This change does not alter scanner eligibility, raw archive publication,
replay-header version support, analyzer behavior, derived artifact publication,
training data, evaluation, or champion state.
