# Data-pipeline audit optimization-safety contract

HaxLab audit modules are integrity verifiers. Their validation behavior must not weaken when Python runs with optimization enabled.

The contract recursively covers every `*_audit.py` under ingestion, learning, and runtime.

## Rejected constructs

Audit modules may not use:

- Python `assert` statements for validation;
- `__debug__`-dependent control flow;
- `sys.flags.optimize`-dependent behavior, including imported aliases.

Python can remove `assert` statements and change `__debug__` when invoked with `-O`. An integrity check implemented on those surfaces would therefore not be a stable contract.

## Allowed validation

Explicit fail-closed validation remains valid, for example normal `if` conditions that raise an audit-specific exception or route to an explicit failure helper.

This is an additive validation-only contract. It does not modify any auditor, producer, runtime state, evaluation code, model, or champion state.
