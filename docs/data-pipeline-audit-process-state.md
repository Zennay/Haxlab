# Data-pipeline audit process-state isolation

Data-pipeline auditors are validators. Running one must not silently reconfigure the Python process that hosts the audit.

This contract covers every `*_audit.py` module below:

- `src/haxlab/ingestion/`
- `src/haxlab/learning/`
- `src/haxlab/runtime/`

The regression rejects writes to process-global state through:

- working-directory mutation (`os.chdir`, `os.fchdir`);
- environment mutation (`os.environ`, `putenv`, `unsetenv`);
- import-state mutation (`sys.path`, `sys.modules`);
- signal-handler mutation;
- process umask mutation;
- locale mutation.

Read-only inspection such as `os.getcwd()`, `os.environ.get(...)`, `signal.getsignal(...)`, and reading `sys.path` remains valid.

## Boundary

This is intentionally separate from existing audit contracts:

- import hermeticity prevents side effects while importing auditor modules;
- ambient determinism prevents decisions from depending on volatile ambient inputs;
- execution read-only protects audited evidence and filesystem state;
- exception-boundary validation prevents broad exception swallowing;
- dynamic-code loading validation prevents executable loading/evaluation.

Process-state isolation instead protects the host process itself from mutation during auditor execution. The contract is additive and changes no producer, runtime, ingestion, learning, model, evaluation, or champion behavior.
