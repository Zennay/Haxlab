# Ingest CLI input boundary

The public `haxlab ingest` command validates operator-controlled inputs before calling the ingestion producer.

## Export-root contract

The positional `export_root` must already be an existing real directory. Missing paths, regular files, and symlinked directory roots are rejected by `argparse` with exit status 2 before `run_import()` is called.

This is an operator-facing preflight, not the security authority for source traversal. Ingestion discovery still revalidates the root and individual replay paths so a filesystem race after argument parsing fails closed at the producer boundary.

## Match-confidence contract

Accepted `--minimum-match-confidence` values must:

- be expressed without surrounding whitespace;
- parse as a finite floating-point number;
- fall within the closed interval `[0, 1]`.

The default remains `0.65`.

Values such as `nan`, `inf`, `-inf`, negatives, and values above `1` are rejected by `argparse` with exit status 2. This keeps malformed operator input from reaching `run_import()`.

## Ownership boundary

This CLI contract does not replace producer validation. Direct Python callers of `run_import()` still require their own fail-closed validation at the ingestion boundary.

PR #74 owns producer-side validation and publication hardening in `src/haxlab/ingestion/pipeline.py` and `tests/test_pipeline.py`; this contract intentionally does not modify those files.
