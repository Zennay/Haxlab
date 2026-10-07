# Data-pipeline audit determinism contract

HaxLab's read-only data-pipeline auditors are evidence verifiers. Their conclusions must be reproducible from the explicit files, rows and values they validate, not from ambient process state.

This validation contract recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

New audit modules added under those trees are covered automatically.

## Forbidden ambient inputs

Audit decision code may not depend on:

- Python's process-randomized builtin `hash()`;
- environment variables through `os.environ` or `os.getenv()`;
- OS entropy through `os.urandom()`;
- wall, monotonic, performance, process or thread clock getters;
- `datetime.now()`, `utcnow()`, `today()` or `date.today()`;
- `random` or `secrets` entropy;
- generated UUID APIs such as `uuid1`, `uuid4`, `uuid6`, `uuid7` or `uuid8`.

Import aliases, chained assignment aliases and loaded callable/module references are resolved by the contract. Constant `getattr(...)` indirection is resolved as well, including an aliased `builtins.getattr`, so an ambient source cannot be hidden behind another local name before use. Direct tuple/list unpacking of aliasable references is covered. Wildcard imports from sensitive modules are rejected.

## Allowed deterministic operations

The contract deliberately permits deterministic transformations of explicit evidence, including:

- `datetime.fromisoformat(...)` for timestamps supplied by the artifact being audited;
- cryptographic hashes such as `hashlib.sha256(...)`;
- parsing explicit UUID values with `uuid.UUID(...)`;
- sorting, canonical serialization and other deterministic local computation.

## Separation from import hermeticity

Issue #180 owns import-time side effects, filesystem mutation, network and subprocess hermeticity. This contract does not duplicate that surface. It only prevents audit results from changing because the same evidence was checked in a different process environment or at a different time.

Issue #378 hardens the already-integrated determinism boundary against assignment and constant-`getattr` alias indirection. No producer, auditor, runtime state, ingestion, learning, evaluation, model or champion implementation is modified by this lane.
