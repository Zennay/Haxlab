# Data-pipeline audit warning-state isolation

HaxLab data-pipeline audit modules are integrity verifiers. They may inspect explicit evidence and emit warnings, but they must not alter Python's process-global warning policy.

## Scope

The contract recursively scans every `*_audit.py` module below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

It is intentionally additive and does not modify audit implementations or producer/runtime behavior.

## Forbidden warning-policy mutation

Audit modules must not:

- call `warnings.filterwarnings(...)`, `warnings.simplefilter(...)`, `warnings.resetwarnings()`, or `warnings.catch_warnings()`;
- mutate `warnings.filters`, `warnings.defaultaction`, `warnings.onceregistry`, `warnings._onceregistry`, `warnings._defaultaction`, `warnings.showwarning`, or `warnings.formatwarning`;
- mutate those objects through local aliases, constant-`getattr(...)` aliases, reflected `setattr`/`delattr`, bound or unbound list mutators, subscript writes/deletes, augmented assignment, or `operator.setitem`/related functional mutation;
- call the private `warnings._filters_mutated()` policy-version mutator;
- use `from warnings import *`, because that makes warning-policy mutators statically ambiguous.

The contract resolves ordinary imports, import aliases, assignment aliases, and constant-string `getattr(...)` indirection before evaluating calls.

## Allowed behavior

Read-only inspection remains valid. An auditor may:

- call `warnings.warn(...)`;
- inspect `warnings.filters` or `warnings.defaultaction`;
- bind a local alias to warning state for read-only inspection;
- copy warning state into a local container and freely mutate that copy.

## Why this matters

Warning filters and registries are shared interpreter state. If an audit routine changes them, later validation in the same process can suppress, promote, or otherwise alter diagnostics based on call order rather than explicit evidence. HaxLab's audit layer should remain deterministic and side-effect-minimal, so warning policy belongs to the same protected boundary as other process-global state.

## Proof

The focused regression is:

`tests/test_data_pipeline_audit_warning_state_contract.py`

The self-hosted exact-head workflow is:

`.github/workflows/data-pipeline-audit-warning-state-proof.yml`

Integration requires the focused exact-head proof and normal HaxLab CI to be terminal green on the same candidate head.
