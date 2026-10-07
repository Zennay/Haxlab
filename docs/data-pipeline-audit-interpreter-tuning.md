# Data-pipeline audit interpreter-tuning isolation

HaxLab data-pipeline auditors are evidence verifiers. They may inspect interpreter tuning, but they must not change interpreter-wide settings that can alter later validation behavior.

## Scope

The contract recursively scans every `*_audit.py` module below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

It is additive validation only. Producer/runtime/learning behavior, evaluation, models, champion state and thresholds remain untouched.

## Forbidden mutations

Audit modules must not invoke:

- `sys.setrecursionlimit(...)`;
- `sys.setswitchinterval(...)`;
- `sys.set_int_max_str_digits(...)`;
- `threading.stack_size(...)` when an argument is supplied.

The scanner resolves normal imports, direct imports, aliases, chained/tuple assignment aliases and constant-string `getattr(...)`. Dynamic `getattr(sys/threading, name)` capability selection and wildcard imports from `sys` or `threading` are rejected because mutator provenance becomes statically ambiguous.

## Allowed inspection

Read-only inspection remains valid, including:

- `sys.getrecursionlimit()`;
- `sys.getswitchinterval()`;
- `sys.get_int_max_str_digits()`;
- `threading.stack_size()` with no argument.

## Why this matters

These APIs change process-wide interpreter behavior. A verifier that changes recursion depth, scheduling cadence, integer-string conversion limits or the default stack size for later threads can make exact-head evidence depend on audit order instead of explicit repository and data inputs.

## Proof

Focused regression:

`tests/test_data_pipeline_audit_interpreter_tuning_contract.py`

Exact-head self-hosted workflow:

`.github/workflows/data-pipeline-audit-interpreter-tuning-proof.yml`

Integration requires the focused proof and normal HaxLab CI to be terminal green on the same immutable candidate head.
