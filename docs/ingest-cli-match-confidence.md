# Ingest CLI match-confidence boundary

The public `haxlab ingest` command validates `--minimum-match-confidence` before calling the ingestion producer.

## Contract

Accepted values must:

- be expressed without surrounding whitespace;
- parse as a finite floating-point number;
- fall within the closed interval `[0, 1]`.

The default remains `0.65`.

Values such as `nan`, `inf`, `-inf`, negatives, and values above `1` are rejected by `argparse` with exit status 2. This keeps malformed operator input from reaching `run_import()`.

## Ownership boundary

This CLI contract does not replace producer validation. Direct Python callers of `run_import()` still require their own fail-closed validation at the ingestion boundary.

PR #74 owns producer-side validation and publication hardening in `src/haxlab/ingestion/pipeline.py` and `tests/test_pipeline.py`; this contract intentionally does not modify those files.
