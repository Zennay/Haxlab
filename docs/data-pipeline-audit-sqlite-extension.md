# Data-pipeline SQLite extension-loading contract

HaxLab data-pipeline auditors are deterministic evidence verifiers. They must not load SQLite extensions, because an extension can execute native code selected from ambient filesystem state and would bypass the Python import, subprocess, network and read-only boundaries already enforced around audit modules.

This additive contract recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

## Forbidden surfaces

The contract rejects:

- direct `connection.enable_load_extension(...)` calls;
- direct `connection.load_extension(...)` calls;
- assigned aliases of either callable;
- constant-`getattr(...)` aliases of either callable, including imported `builtins.getattr`;
- literal SQL passed to `execute`, `executemany` or `executescript` that invokes SQLite's `load_extension(...)` SQL function.

## Allowed SQLite behavior

Auditors may continue to open SQLite state read-only, enable `PRAGMA query_only=ON`, run `PRAGMA quick_check`, execute deterministic `SELECT` queries and validate the returned evidence.

This contract intentionally does not alter any production auditor or runtime code. It is a validation boundary only.

## Ownership separation

Issue #474 owns only SQLite extension loading. Issue #459 separately owns dynamic SQL classification in the existing read-only contract. The two lanes are file-disjoint so they can progress independently.
