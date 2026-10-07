# Source-bundle descriptor read failures

Source-bundle receipt generation and persisted-receipt verification use descriptor-backed reads so source and receipt identity remain pinned while bytes are consumed.

## Error contract

An operating-system read failure is a data-pipeline integrity failure, not an uncaught runtime exception.

- Source-file `os.read()` failures are translated to `SourceBundleReceiptError` with the relative source path.
- Persisted-receipt `os.read()` failures are translated to `SourceBundleVerifyError`.
- The receipt and verifier CLI entrypoints therefore keep their existing exit code `2` and machine-readable JSON failure payloads.
- Successful reads, receipt schema, hashes, source ordering and verification comparison semantics are unchanged.

This contract does not alter ingestion traversal, replay/report matching, source selection, derived artifacts, runtime processing, learning, evaluation or promotion state.
