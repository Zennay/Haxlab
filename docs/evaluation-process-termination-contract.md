# Evaluation host-process termination contract

HaxLab evaluation modules are used both as CLI entry points and as imported gate
libraries. Library evaluation must fail closed by returning/raising ordinary
validation errors; it must not terminate the Python process that hosts the gate.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` may use
exactly one process-exit shape:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

The guard must be top-level, the `SystemExit` must be a direct child of that
guard, and the exit status must come from an argument-free `main()` call.

Every other host-process termination/control path is rejected, including:

- `sys.exit` and aliases;
- builtin `exit` / `quit`;
- `os._exit` and `os.abort`;
- `os.exec*` current-process image replacement;
- `os.kill` / `os.killpg`;
- `signal.raise_signal` / `signal.pthread_kill`;
- library-level `SystemExit` or `KeyboardInterrupt`, including imported
  aliases.

## Boundary

This is separate from the current evaluation contracts:

- #109/#110 owns required CLI/schema shape and proves selected commands expose a
  top-level `main()` guard;
- #200/#201 owns mutation of process-global state such as cwd, environment,
  signal handlers, locale and interpreter hooks;
- network/subprocess hermeticity owns child-process creation and network access;
- exception-boundary validation owns broad exception swallowing.

This contract instead guarantees that imported evaluation logic cannot terminate
the validator host. It changes no evaluation implementation, model, threshold,
champion pointer or canonical Arena-v2 workflow.

## Proof

`tests/test_evaluation_process_termination_contract.py` recursively scans the
canonical evaluation tree with Python ASTs. Its self-tests cover direct calls,
import aliases, signal/process-control calls, non-canonical termination
exceptions, nested fake main guards and the one allowed canonical CLI shape.

The branch-scoped exact-head workflow runs the focused contract and then the 12
adjacent canonical Arena-v2 evaluation regression files used by the existing
verification bundle (excluding owner-reserved blocker #92). A green marker is
emitted only after both layers pass.

This remains narrow validation evidence; it does not authorize PR #19 merge or
champion promotion.
