# Data-pipeline auditor runtime output-purity contract

HaxLab data-pipeline auditors are both library integrity checks and machine-readable
CLI tools. Library calls must communicate through return values or explicit evidence,
not through accidental stdout/stderr side effects that can corrupt a composed receipt
or caller output.

## Required invariant

Every production `*_audit.py` module recursively below
`src/haxlab/ingestion/`, `src/haxlab/learning/` and
`src/haxlab/runtime/` must remain silent on stdout/stderr outside the top-level
runtime body of its CLI `main()`.

The contract rejects outside that CLI body:

- builtin `print(...)`, including imported or assigned aliases;
- `write` / `writelines` through `sys.stdout`, `sys.stderr`,
  `sys.__stdout__`, `sys.__stderr__` and their buffers;
- constant `getattr(...)` spellings of tracked output paths;
- `os.write(1|2, ...)`, including imported/assigned aliases;
- `os.fdopen(1|2, ...).write(...)` and `.writelines(...)`.

Only the direct body of a module-level `main()` receives the CLI exemption.
A class method named `main`, a nested helper/lambda, or a decorator/default
expression does not.

Intentional top-level CLI rendering, pure return values and writes to non-stdio file
descriptors remain allowed.

## Scope and non-overlap

This is an additive validation contract. It does not modify any auditor implementation,
producer, runtime state/schema, ingestion/learning product code, evaluation code,
model, threshold or champion state.

It is intentionally separate from existing data-pipeline contracts for import purity,
network/subprocess hermeticity, ambient determinism, read-only execution, safe
deserialization, exception boundaries, dynamic code loading, process-global state,
host-process termination/liveness and auditor import cycles. Evaluation output purity
is independently owned by #249/#252.

## Proof

`tests/test_data_pipeline_audit_output_purity.py` parses the complete current
data-pipeline auditor set with Python ASTs. Self-tests prove direct output, stream
aliases, constant reflection, original stdio streams, buffers, `os.write`,
`os.fdopen`, nested functions/lambdas and fake `main` methods fail closed while
valid CLI output and pure library returns stay accepted.

Normal HaxLab pull-request CI is the exact-head acceptance path. A green contract does
not authorize unrelated staged data-pipeline lanes to merge; it only freezes this
cross-cutting library-output invariant.
