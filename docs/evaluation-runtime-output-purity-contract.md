# Evaluation runtime output-purity contract

HaxLab evaluation functions are consumed as library APIs by automation. Their
return values and explicit evidence files must remain the contract; accidental
stdout/stderr output from library code must not become a hidden side channel or
corrupt machine-readable caller output.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
emit directly to stdout or stderr outside their top-level `main()` CLI
entrypoints.

The contract rejects:

- builtin `print(...)` outside `main()`;
- `sys.stdout` / `sys.stderr` plus `sys.__stdout__` / `sys.__stderr__` `write` or `writelines` calls, including `.buffer` equivalents, outside `main()`;
- aliases and constant-`getattr(...)` spellings of those output paths;
- direct `os.write(1, ...)` / `os.write(2, ...)` and `os.fdopen(1|2, ...).write(...)` or `.writelines(...)` outside `main()`.

Intentional CLI rendering inside `main()`, pure return values, and writes to
non-stdio file descriptors remain allowed.

## Boundary

This contract is separate from:

- #160/#162 import purity, which prevents output and mutation while a module is
  imported;
- #221/#222 foreground liveness and non-interactivity;
- #200/#201 process-global state mutation;
- #245/#248 destructive filesystem mutation;
- #94 scenario-source provenance and persistence access.

It changes no evaluation implementation, model, threshold, champion pointer,
scenario source, calibration input, evidence format or canonical Arena-v2
workflow.

## Proof

`tests/test_evaluation_runtime_output_purity_contract.py` recursively parses
the evaluation package with Python ASTs. Its self-tests cover direct output,
import aliases, assignment aliases, constant `getattr`, primary/original stdout/stderr streams,
`writelines`, stdio buffers and file-descriptor/fdopen output while freezing allowed CLI output and pure
library return behavior.

The branch-scoped proof workflow verifies that the checked SHA is still the
live branch head, compiles the package and contract, runs the focused output
purity suite, then runs the adjacent canonical Arena-v2 evaluation regressions
that are not blocked by owner-reserved #92.

A green result is validation evidence only. It does not authorize PR #19 merge,
canonical resync or champion promotion.
