# Evaluation optimization-safety contract

HaxLab evaluation and promotion decisions are safety-critical control logic. Python's `assert` statement is not an authorization or validation primitive: running the interpreter with optimization enabled (`python -O` or `PYTHONOPTIMIZE`) removes assertion statements from bytecode.

## Contract

Every production Python module directly under `src/haxlab/evaluation/` must therefore implement input validation, invariant checks, and promotion rejection with explicit control flow and exceptions/decision reasons. Production modules in that package must not contain `assert` statements.

The additive regression `tests/test_evaluation_no_assert_contract.py` parses every evaluation module with the Python AST and fails with exact file/line evidence if an `ast.Assert` node appears.

This contract does not change evaluation thresholds, calibration inputs, model/champion state, workflow sequencing, or any existing Arena-v2 implementation. It exists only to prevent a future refactor from accidentally creating checks that disappear under optimized Python execution.

## Integration

This lane is stacked directly on the canonical Arena-v2 head and should remain isolated from active owners for calibration runtime, scenario provenance, gate receipts, resync, evidence I/O, policy config, promotion hardening, and cross-workflow validation. Exact-head CI is required before integration.

The branch-scoped proof must execute on the exact pushed commit SHA; superseded heads are not acceptance evidence.
