# Data-pipeline audit garbage-collector state isolation

HaxLab data-pipeline auditors are evidence verifiers. They may inspect Python garbage-collector state, but they must not reconfigure the collector, mutate its callback registry, freeze/unfreeze process objects, or force collection while judging evidence.

## Scope

The contract recursively scans every `*_audit.py` module below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

It is additive validation only. Producer/runtime/learning behavior, evaluation, models, champion state and thresholds remain untouched.

## Forbidden mutations

Audit modules must not invoke:

- `gc.enable()` or `gc.disable()`;
- `gc.set_debug(...)` or `gc.set_threshold(...)`;
- `gc.freeze()` or `gc.unfreeze()`;
- `gc.collect(...)`;
- mutating list operations or assignment/deletion against `gc.callbacks` or `gc.garbage`, including direct dunder/setattr/delattr write paths.

The scanner resolves normal imports, direct imports, aliases, chained/tuple assignment aliases and constant-string `getattr(...)`. Dynamic `getattr(gc, name)` capability selection and wildcard imports from `gc` fail closed because mutator provenance cannot be proven statically.

## Allowed inspection

Read-only inspection remains valid, including:

- `gc.isenabled()`, `gc.get_debug()`, and `gc.get_threshold()`;
- `gc.get_count()` and `gc.get_stats()`;
- `gc.get_freeze_count()`;
- snapshotting GC registries for observation, for example `tuple(gc.callbacks)` or `tuple(gc.garbage)`.

## Why this matters

Garbage collection is process-global. Reconfiguring it, changing callback/garbage registries, freezing objects or forcing a collection can affect later validation, trigger finalizers, and make exact-head evidence depend on audit order rather than explicit repository and data inputs.

## Proof

Focused regression:

`tests/test_data_pipeline_audit_gc_state_contract.py`

Exact-head self-hosted workflow:

`.github/workflows/data-pipeline-audit-gc-state-proof.yml`

Integration requires the focused proof and normal HaxLab CI to be terminal green on the same immutable candidate head.
