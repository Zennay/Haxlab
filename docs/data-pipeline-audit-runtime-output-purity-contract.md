# Data-pipeline auditor runtime output-purity contract

HaxLab data-pipeline audit functions are consumed as library APIs by workers and
automation. Their return values and explicit evidence artifacts must remain the
contract; accidental stdout/stderr writes from helper code must not become a
hidden side channel or corrupt machine-readable caller output.

## Required invariant

Every Python module recursively named `*_audit.py` below `src/haxlab/` must
avoid direct stdout/stderr emission outside the top-level runtime body of its
module-level `main()` CLI entrypoint.

The contract rejects:

- builtin `print(...)` outside CLI `main()`;
- `sys.stdout`, `sys.stderr`, `sys.__stdout__`, and `sys.__stderr__`
  `write`/`writelines` calls, including their `.buffer` forms;
- aliases and constant-`getattr(...)` spellings of those output surfaces;
- `os.write(1|2, ...)`;
- direct or assigned `os.fdopen(1|2, ...)` streams followed by `.write(...)` or `.writelines(...)`;
- output from class methods or nested helpers/lambdas even when named
  `main`;
- output in decorators, default values, and annotations evaluated outside the
  runtime body of CLI `main()`.

Intentional CLI rendering inside top-level module `main()`, pure return values,
and writes to non-stdio descriptors remain allowed.

## Why this is distinct

The integrated data-pipeline hermeticity contract makes module imports silent
and blocks network/process execution statically. It does not forbid output after
a caller explicitly invokes an audit function.

The integrated mutable-default contract (#269/#270) guards hidden call-order
state, while global-state, determinism, read-only, liveness/background,
process-termination, dependency-direction, exception, and safe-deserialization
contracts own separate invariants.

This lane changes no auditor/product implementation, evidence format, source
receipt, runtime state, model/evaluation logic, or champion state.

## Proof

`tests/test_data_pipeline_audit_runtime_output_purity_contract.py` recursively
parses every current and future `*_audit.py` module under `src/haxlab/`.
Self-tests cover direct output, import/assignment aliases, constant `getattr`,
primary/original streams, `writelines`, stdio buffers, direct and assigned
`fdopen` stdio handles, file-descriptor output, nested/class `main` lookalikes,
and signature/decorator side effects.

The branch-scoped self-hosted workflow verifies the exact live branch head,
compiles the repository, runs the focused contract, then reruns the integrated
data-pipeline audit safety bundle before emitting
`HAXLAB_DATA_PIPELINE_AUDIT_OUTPUT_PURITY_RESULT=green`.
