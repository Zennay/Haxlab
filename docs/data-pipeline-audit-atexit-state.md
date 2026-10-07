# Data-pipeline audit atexit-state isolation

HaxLab data-pipeline auditors are evidence verifiers. They must not leave process-exit callback state behind for later validation or execute another component's registered exit callbacks.

## Scope

The contract recursively scans every `*_audit.py` below `src/haxlab/ingestion/`, `src/haxlab/learning/`, and `src/haxlab/runtime/`.

It is additive validation only and does not modify audit/product implementation.

## Forbidden behavior

Audit modules must not call:

- `atexit.register(...)`;
- `atexit.unregister(...)`;
- `atexit._clear()`;
- `atexit._run_exitfuncs()`.

The scanner resolves direct imports, module aliases, assignment/chained/tuple aliases, and constant-string `getattr(...)` indirection. `from atexit import *` is rejected because it exposes process-global registry mutators without statically explicit provenance.

Local callback registries remain allowed.

## Why this matters

The atexit registry lives for the process lifetime. An auditor that changes it can make later exact-head evidence depend on call order or can run cleanup callbacks earlier than their owner intended. Data-pipeline verification should not install, remove, clear, or execute process-exit callbacks.

## Proof

Focused regression: `tests/test_data_pipeline_audit_atexit_state_contract.py`.

Exact-head self-hosted workflow: `.github/workflows/data-pipeline-audit-atexit-state-proof.yml`.

Integration requires the focused proof and normal HaxLab CI to be terminal green on the same immutable candidate head.
