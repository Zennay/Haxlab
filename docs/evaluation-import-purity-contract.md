# Evaluation import-purity contract

The Python evaluation package is a decision boundary. Importing it must be safe
for callers that only need library symbols and must not itself perform work.

## Contract

Every Python module recursively present under `src/haxlab/evaluation/` must:

- import successfully in a fresh Python process;
- emit no stdout or stderr during import;
- perform no filesystem writes or mutating filesystem operations during import;
- create no files or directories in an otherwise-empty working directory.

The probe disables Python bytecode writes so interpreter cache creation is not
mistaken for product behavior. Read-only source/module loading remains allowed.

This contract is intentionally narrower than runtime hermeticity. It does not
change gate behavior, thresholds, models, evidence formats, source suites,
calibration inputs, champion state, or workflow-owned Arena execution. Existing
determinism, optimization-safety, hermeticity, and deserialization contracts
remain independent.

## Why this is fail-closed

Evaluation modules are imported by tests, CLIs, orchestration, and future
integrators. A newly added top-level write, directory mutation, print, or other
import-time side effect can otherwise occur before a caller reaches an explicit
validation gate. The regression discovers modules recursively, so new modules
join the contract automatically.

## Validation

`tests/test_evaluation_import_purity_contract.py` launches each module in an
isolated subprocess with an audit hook that rejects write-capable `open` calls
and mutating `os.*` filesystem events. Captured stdout/stderr and the empty
working directory are checked after every import.

The dedicated self-hosted workflow binds proof to the exact branch SHA and emits
`HAXLAB_EVALUATION_IMPORT_PURITY_RESULT=green` only after compile and regression
success.
