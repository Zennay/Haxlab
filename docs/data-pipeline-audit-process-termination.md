# Data-pipeline auditor host-process termination contract

HaxLab data-pipeline auditors are used both as CLI tools and as imported integrity libraries. Imported audit logic must fail closed through ordinary validation errors and return codes; it must never terminate, signal, or replace the Python process that hosts the validator.

## Required invariant

Python modules recursively named `*_audit.py` below `src/haxlab/ingestion/`, `src/haxlab/learning/` and `src/haxlab/runtime/` may use exactly one process-exit shape:

```python
if __name__ == "__main__":
    raise SystemExit(main())
```

The guard must be top-level, the `SystemExit` must be a direct child of that guard, and its status must come from an argument-free `main()` call.

Every other host-process termination/control path is rejected, including:

- `sys.exit` and aliases;
- builtin `exit` / `quit`;
- `os._exit` and `os.abort`;
- `os.exec*` current-process image replacement;
- `os.kill` / `os.killpg`;
- `signal.raise_signal` / `signal.pthread_kill`;
- library-level `SystemExit` or `KeyboardInterrupt`, including imported and assignment aliases.

## Boundary

This additive contract changes no auditor implementation. It is separate from foreground liveness (#223/#224), hidden background execution (#225/#227), broad-exception handling, process/network hermeticity, and process-global state mutation.

## Proof

`tests/test_data_pipeline_audit_process_termination_contract.py` recursively scans the data-pipeline auditor tree with Python ASTs. Self-tests cover direct calls, imports, assignment/annotated aliases, constant-`getattr(...)`, signal/process-control calls, non-canonical termination exceptions, nested fake main guards, and the one allowed canonical CLI shape.

The branch-scoped self-hosted workflow verifies exact checkout and live branch-head identity, precompiles the repository for isolated-import regressions, runs the focused contract, and reruns the foreground-liveness, hidden-background-execution, and existing adjacent audit contracts.

Only proof from the final exact branch SHA counts as acceptance evidence.
