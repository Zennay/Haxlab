# Data-pipeline process-environment immutability contract

HaxLab data-pipeline auditors are evidence verifiers. They must not change process-wide environment variables because those changes survive the current function call and can alter later validation, library behavior or path/config resolution in the same worker process.

The existing module-global-state contract covers mutable containers owned by an audit module. This contract separately protects the operating-system environment surface.

It recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

## Forbidden mutation

The contract rejects:

- assignment, deletion and augmented assignment through `os.environ` or `os.environb`;
- mutating mapping calls including `update`, `clear`, `pop`, `popitem`, `setdefault`, `__setitem__`, `__delitem__` and `__ior__`;
- `os.putenv(...)` and `os.unsetenv(...)`;
- functional `operator.setitem(...)`, `operator.delitem(...)` and `operator.ior(...)` when their target is the process environment;
- simple aliases and constant-`getattr(...)` aliases of these capabilities.

Environment reads remain allowed, including `os.getenv`, `os.environ.get`, subscript reads, binding a local read-only alias such as `env = os.environ`, and copying the environment into a local mapping that may then be mutated locally.

## Ownership separation

Issue #486 owns only process-environment mutation by data-pipeline audit modules. It is additive and does not modify production auditor or producer behavior. It remains separate from module-global-state, mmap and native-FFI contracts.
