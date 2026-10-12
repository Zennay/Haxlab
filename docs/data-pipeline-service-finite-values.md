# Data-pipeline service numeric argument contract

HaxLab's deployed ingestion, worker and analyzer units pass numeric arguments as literal ExecStart values. The repository validates these values in tests/test_data_pipeline_service_contract.py before a changed unit is eligible for integration.

## Invariant

Every registered numeric service flag must parse to a **finite, strictly positive** Python float. Checking only `float(raw) > 0` is insufficient: `float('inf') > 0` is true, and `float('1e9999')` becomes positive infinity. Neither may satisfy the service contract.

The covered flags are:

- Ingest: `--interval`, `--minimum-file-age`.
- Worker: `--batch-size`, `--interval`.
- Analyzer: `--workers`, `--batch-size`, `--sample-every-ticks`, `--timeout-seconds`, `--interval`.

The focused regression rejects positive and negative infinity, overflowed scientific notation, NaN, zero, negative values and malformed strings. Finite positive decimal and exponent forms remain accepted.

This static deployment-contract gate is not a replacement for runtime CLI per-flag integer/float validation. It does not change deployed units, replay data, model state or VPS state. Passing this contract alone is not deployment evidence.

Related issue: https://github.com/Zennay/Haxlab/issues/552
