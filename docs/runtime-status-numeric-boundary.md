# Runtime status numeric admission

`haxlab-status` reads counters, durations and rates from a runtime snapshot. A status consumer must never crash with a raw Python `OverflowError` merely because an unexpected native integer is too large for a finite floating-point representation.

The status parser keeps its existing native-type checks. For the three finite metrics (`duration_seconds_probed`, `probe_rate_per_minute_5m`, `analysis_rate_per_minute_5m`), it converts each numeric value once inside an explicit exception boundary, rejects unrepresentable magnitudes, negatives, NaN and infinities, and reports the familiar `ValueError("invalid_status_snapshot:<field>")` consistently.

Valid existing snapshots retain the same output shape and calculations. This is a read-only status consumption change, not a ledger schema migration or worker/runtime/service mutation. The prior source fix from PR-less `feature/data-pipeline-status-finite-overflow-20261007` is reused on fresh current main without carrying over its QA or proof artifacts. Existing automatic CI and independent review govern merge and deployment.
