# Ingestion runtime-output purity contract

HaxLab's ordinary ingestion modules are library code first. Parser, discovery,
matching and producer helpers return evidence to their callers; they must not
write diagnostics or payloads to process stdout/stderr as a hidden side effect.

## Allowed CLI boundary

A module-level `main()` may intentionally render machine-readable CLI output.
Only the direct runtime body of that top-level function is exempt.

The exemption does not extend to:
- class methods named `main`;
- nested helpers, lambdas or lazy generator expressions created by `main`;
- decorators, defaults, annotations or other function-signature expressions;
- library helpers called independently by another Python caller.

## Guarded output surfaces

`tests/test_ingestion_runtime_output_purity_contract.py` recursively scans
every Python module below `src/haxlab/ingestion/` and rejects output outside
that CLI boundary through:
- builtin `print`, including imported, assigned and chained-assignment aliases;
- stdout/stderr and original-stream `write` / `writelines`, including buffers;
- constant `getattr(...)` aliases;
- `os.write(1|2, ...)`;
- direct or assigned `os.fdopen(1|2, ...)` output streams.

Pure return values and non-stdio descriptor writes remain valid.

This is separate from the ingestion hermeticity contract (#428/#429), which
protects network/process/import-time side effects. This contract protects
runtime output purity of ordinary ingestion APIs.

## Proof

The branch-scoped workflow verifies the exact live branch head on the
self-hosted HaxLab runner, compiles source/tests, runs this contract and
adjacent ingestion regressions. Normal HaxLab CI remains required before
integration.
