# Data-pipeline audit exception boundary

Data-pipeline auditors are integrity verifiers. A broad exception boundary must never turn unexpected malformed evidence into a clean result.

`tests/test_data_pipeline_audit_exception_boundary.py` recursively scans every `*_audit.py` module under ingestion, learning, and runtime.

## Contract

Broad handlers — bare `except`, `Exception`, `BaseException`, or tuples containing either — are accepted only when their control flow is fail-closed:

- the handler ends by re-raising; or
- the handler ends by calling the local audit-failure primitive `_fail(...)`; or
- a final conditional has fail-closed termination on both branches.

Within a broad handler, `pass`, `continue`, `break`, and `return` are rejected even when nested. Explicit narrow exception handlers remain outside this contract.

This preserves the existing cleanup-and-reraise path in shard auditing and the explicit audit-failure conversion used by baseline auditing while preventing future broad-exception swallowing.

## Scope

This is additive validation only. It changes no auditor implementation, producer, runtime state, ingestion flow, learning artifact, evaluation logic, model, or champion state.
