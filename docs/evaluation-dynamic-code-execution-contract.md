# Evaluation dynamic-code execution contract

HaxLab evaluation gates must remain statically reviewable. Production evaluation
code must not compile or execute runtime-supplied Python source.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not:

- call the built-in `eval(...)`, `exec(...)`, or `compile(...)`;
- reach those builtins through direct imports, module aliases, assignment aliases,
  annotated/named assignment aliases, constant `getattr(...)`, or constant
  `__builtins__` / `builtins.__dict__` lookup;
- wildcard-import `builtins`, which would make those execution primitives
  available without an auditable binding;
- import the interactive/runtime compiler helper modules `code` or `codeop`.

Ordinary Python functions, static imports, AST parsing, and unrelated methods
named `eval` or helpers named `compile_report` remain allowed.

## Why this is distinct

The executable-deserialization contract (#155/#157) prevents dangerous object
loading from serialized evidence. The dynamic-loading contract (#170/#171)
prevents runtime module/loader injection. The native-FFI contract (#279/#281)
prevents explicit native-library execution. None of those boundaries rejects
plain built-in `eval`, `exec`, or `compile`, which can execute code entirely
inside the current Python process without deserialization, importlib, FFI,
network access, or a child process.

This contract is also separate from host-process state (#200/#201) and
memoization state (#323/#326): it forbids creation/execution of dynamic Python
code rather than persistence or mutation of runtime state.

## Proof

`tests/test_evaluation_dynamic_code_execution_contract.py` recursively parses
the complete evaluation package. Focused regressions cover direct builtins,
builtins-module access, direct imports, assignment aliases, annotated/named
assignment aliases, constant reflection/mapping lookup, wildcard builtins imports,
and `code`/`codeop` imports while preserving ordinary static code.

The branch-scoped self-hosted proof compiles the evaluation package and contract,
runs the focused test, then runs adjacent canonical Arena-v2 evaluation
regressions before emitting
`HAXLAB_EVALUATION_DYNAMIC_CODE_EXECUTION_RESULT=green`.

A green result proves only this narrow invariant. It does not authorize canonical
Arena-v2 PR #19 integration/resync or champion promotion.
