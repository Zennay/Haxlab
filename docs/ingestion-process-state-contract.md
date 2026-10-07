# Ingestion process-state isolation contract

HaxLab ingestion helpers are called inside importer and orchestration processes.
Running an ingestion parser, matcher, discovery helper or producer must not
silently reconfigure the host process for later work.

## Protected process state

`tests/test_ingestion_process_state_contract.py` recursively scans every Python
module below `src/haxlab/ingestion/` and rejects mutation of:

- the current working directory via `os.chdir` / `os.fchdir`;
- environment state via `os.environ`, `putenv` and `unsetenv`;
- Python import state via `sys.path` and `sys.modules`;
- signal handlers and wakeup descriptors;
- the process umask;
- locale configuration;
- interpreter tracing/profile/recursion/switch-interval hooks.

The contract resolves direct imports, imported aliases, assignment/chained
aliases and constant `getattr(...)` spellings for guarded state and mutators.
Assignments, deletes and mutating methods rooted in `os.environ`,
`sys.path` and `sys.modules` fail closed.

Read-only inspection such as `os.getcwd()`, `os.environ.get(...)`,
`signal.getsignal(...)`, reading `sys.path`, checking `sys.modules` and
querying locale encoding remains valid.

## Boundary

This contract is intentionally separate from:

- #197/#198, which owns process-state isolation for recursively named
  data-pipeline `*_audit.py` verifier modules only;
- #428/#429, which owns ordinary-ingestion network/process/dynamic-import and
  import-time hermeticity;
- #432/#433, which owns ordinary-ingestion runtime-output purity.

No ingestion implementation, runtime, learning, evaluation, model, champion
or threshold behavior is changed.

## Staging policy

This lane is staged branch-only while the shared self-hosted HaxLab runner is
occupied. It must not open an integration PR or add/dispatch a proof workflow
until the active ingestion validation queue has cleared. Before integration it
still requires an exact-head self-hosted proof, normal HaxLab CI and a fresh
main/ownership drift check.
