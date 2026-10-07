# Data-pipeline audit read-only execution contract

HaxLab audit modules judge already-published evidence. They must never repair, rewrite or delete the evidence they are evaluating, because mutation would collapse the separation between producer and verifier.

This validation-only contract recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

Future audit modules in those trees are included automatically.

## Forbidden mutation surfaces

The contract rejects:

- write-capable or dynamically selected modes passed to built-in/file `open` APIs;
- OS write/create/truncate/append flags;
- filesystem create/delete/rename/link/permission mutation APIs;
- direct `write`, `writelines` and `truncate` calls;
- common serializer/save APIs such as `json.dump`, `pickle.dump`, NumPy save functions and `torch.save`;
- temporary-file creation used as a hidden output channel;
- literal mutating SQL passed to `execute`, `executemany` or `executescript`;
- mutating PRAGMA assignments, except the defensive connection-local `PRAGMA query_only=ON`.

## Allowed read-only behavior

Auditors may still:

- use `os.open(..., O_RDONLY | O_CLOEXEC | O_NOFOLLOW ...)`;
- wrap descriptors with `os.fdopen(..., "rb")`;
- use normal `open(..., "r"/"rb")` and `Path.open(...)` reads;
- parse JSON or other deterministic local evidence;
- run read-only SQL such as `SELECT`, `PRAGMA quick_check` and integrity checks;
- set SQLite `query_only` defensively for the current connection.

The contract intentionally checks both imported aliases and direct API spellings. Regression snippets keep the guard itself honest.

## Ownership separation

Issue #180 owns import-time purity plus network/subprocess hermeticity. Issue #183 owns ambient nondeterminism. This contract only protects execution-time evidence immutability.

No auditor, producer, ingestion/runtime/learning implementation, evaluation gate, model or champion state is changed.
