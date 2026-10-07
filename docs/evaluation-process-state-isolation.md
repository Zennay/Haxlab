# Evaluation process-state isolation contract

HaxLab's evaluation package decides whether challenger evidence is acceptable for
promotion. Those decisions must not silently reconfigure the Python process that
hosts the gate.

## Required invariant

Every Python module recursively below `src/haxlab/evaluation/` must remain free
of process-global state mutation.

The contract rejects:

- working-directory mutation through `os.chdir` / `os.fchdir`;
- environment mutation through `os.environ`, `putenv`, or `unsetenv`;
- import-state mutation through `sys.path` or `sys.modules`;
- signal-handler/timer/mask mutation;
- process umask mutation;
- locale mutation;
- interpreter-hook/global tuning such as `sys.settrace`, `setprofile`,
  `setrecursionlimit`, and `setswitchinterval`;
- direct `setattr` / `delattr` replacement of guarded process-state
  attributes.

Read-only inspection remains valid, including `os.getcwd()`,
`os.environ.get(...)`, `signal.getsignal(...)`, `sys.getrecursionlimit()`,
and reading `sys.path` / `sys.modules`.

## Separation from existing evaluation contracts

This lane is intentionally additive and non-overlapping:

- ambient-determinism validation controls reads from volatile ambient inputs;
- import-purity validation controls side effects while importing a module;
- network/subprocess hermeticity controls external execution and I/O channels;
- control-plane import validation controls first-party dependency direction;
- module-global-state validation controls mutable globals owned by evaluation
  modules;
- safe-deserialization, exception-boundary and dynamic-loading contracts cover
  their own execution hazards.

Process-state isolation instead protects the *host Python process itself* during
normal evaluation execution. It does not prohibit the existing explicit
evaluation CLI output files.

## Proof

`tests/test_evaluation_process_state_contract.py` recursively scans the
production evaluation tree with Python ASTs. Self-tests prove direct calls,
assignments/deletions, mutating methods, imported aliases and
`setattr`/`delattr` replacement are rejected while read-only inspection is
allowed.

The branch-scoped self-hosted workflow verifies the exact event SHA, creates an
isolated environment, compiles the evaluation package plus contract, runs the
focused regression, then reruns the 12 adjacent canonical Arena-v2 evaluation
regression files previously proven by the static-contract verification bundle.
The known owner-reserved runtime-budget collection blocker (#92) remains
deliberately outside this compatibility set. The workflow emits
`HAXLAB_EVALUATION_PROCESS_STATE_RESULT=green` only after both the focused
contract and adjacent canonical regression set pass.

A green result is narrow validation evidence only. It does not authorize
canonical Arena-v2 PR #19 merge or champion promotion.
