# Evaluation safe-deserialization contract

HaxLab promotion and Arena decisions are security-sensitive control decisions. The
Python package under `src/haxlab/evaluation/` must therefore consume explicit
data evidence, not executable Python/object graphs whose deserialization can
invoke constructors, reducers, imports, or arbitrary code.

This contract is intentionally additive. It does not change Arena thresholds,
promotion policy, calibration inputs, model bytes, champion state, or runtime
orchestration.

## Required invariant

Every production Python module recursively below `src/haxlab/evaluation/`
must remain free of executable/object-deserialization mechanisms.

The regression rejects:

- `pickle`, `marshal`, `shelve`, `dill`, `cloudpickle`, and `joblib`
  imports, including aliases and `from ... import ...` forms;
- dynamic-code builtins `eval`, `exec`, and `compile`, including imports
  from `builtins`;
- generic or explicitly unsafe PyYAML loaders. Use `safe_load` or
  `safe_load_all` when YAML is ever required;
- `torch.load` and `torch.jit.load` inside the evaluation decision package;
- `numpy.load` unless the call explicitly passes literal
  `allow_pickle=False`.

Ordinary data-only parsing remains allowed, including JSON, TOML, safe YAML,
plain text, and NumPy arrays with pickle explicitly disabled.

## Boundary

Model/object materialization belongs outside the promotion-decision boundary.
Evaluation code may receive already-derived numeric/statistical evidence and
may validate stable local files, but should not gain the ability to execute
serialization payloads while deciding whether a challenger is acceptable.

This complements, rather than replaces, separate evaluation contracts for
determinism, optimization safety, hermeticity, strict evidence I/O, policy
configuration, exact-head gate receipts, and current-main resync sequencing.

## Proof

`tests/test_evaluation_safe_deserialization_contract.py` recursively scans the
production evaluation tree using Python ASTs. It also regression-tests the
detector itself with representative aliases and allowed/forbidden examples.

The branch-scoped self-hosted proof workflow must:

1. check out the exact GitHub event SHA and verify it;
2. compile the evaluation package and contract test;
3. run the focused regression;
4. emit `HAXLAB_EVALUATION_SAFE_DESERIALIZATION_RESULT=green` only after all
   checks succeed.

A green result proves this narrow static contract only. It does not authorize
PR #19 merge, champion promotion, or reuse of historical Arena evidence on a
different SHA.
