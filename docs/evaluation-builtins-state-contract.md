# Evaluation builtins-state isolation contract

HaxLab evaluation decisions execute inside a Python process that may continue to
run more than one validator. The process-wide `builtins` namespace therefore
belongs to the validation host, not to an individual evaluation call.

## Required invariant

Production Python code recursively below `src/haxlab/evaluation/` must not
mutate the `builtins` module or its backing dictionary.

The contract rejects:

- attribute assignment, deletion, or augmented assignment on `builtins`;
- subscript and in-place mutation through `builtins.__dict__`, an assigned
  alias of that dictionary, or the module-global `__builtins__` reference;
- `setattr` / `delattr` against the `builtins` module;
- mapping mutators such as `update`, `setdefault`, `pop`, `clear`,
  `__setitem__`, `__delitem__`, and `__ior__` on the builtins dictionary;
- unbound `dict` mutation methods and `operator.setitem`,
  `operator.delitem`, or `operator.ior` when their target is the builtins
  dictionary;
- ordinary import and assignment aliases;
- `vars(builtins)` and constant `getattr(..., "__dict__")` spellings;
- static recovery of the same interpreter module through
  `sys.modules["builtins"]` / `sys.modules.get("builtins")`;
- static recovery of the backing dictionary through
  `globals()["__builtins__"]` / `globals().get("__builtins__")`.

Read-only lookup and ordinary builtin calls remain valid. Reading
`vars(builtins)`, calling `getattr(builtins, "open")`, comparing a static
`sys.modules["builtins"]` lookup, importing a builtin symbol for read-only use,
and mutating ordinary local dictionaries are all allowed.

## Why this is separate

The existing evaluation process-state lane protects OS environment state,
`sys.path`, `sys.modules`, signal state, locale and interpreter hooks. Other
lanes protect evaluation-owned module objects, function/class persistent state,
memoization, atexit callbacks, and evidence-object immutability.

None of those contracts owns the interpreter's shared `builtins` namespace.
Reading a `builtins` object out of `sys.modules` does not mutate the
`sys.modules` mapping itself, yet monkeypatching that object can leak from one
evaluation call into later validation code. This lane closes that distinct
state-leak boundary.

## Proof boundary

`tests/test_evaluation_builtins_state_contract.py` performs an additive AST
scan over the complete evaluation package and includes self-tests for direct,
aliased, mapping-backed, magic-`__builtins__`, static-module-lookup,
reflective, unbound-dict, operator and in-place mutation forms.

The branch proof workflow is dispatch-only. It accepts one immutable 40-character
candidate SHA, checks out and verifies that exact SHA and the live validation
branch head, compiles the package and contract, runs the focused contract, then
runs adjacent canonical Arena-v2 evaluation regressions.

This contract changes no evaluation product implementation, policy threshold,
calibration input, model/champion state, promotion decision, or canonical Arena
workflow. A green result is narrow validation evidence only.
