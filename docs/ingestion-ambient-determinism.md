# Ingestion ambient determinism contract

HaxLab's ingestion package is a deterministic transformation boundary: identical raw evidence and explicit configuration must produce identical derived evidence. Ingestion code therefore must not consult ambient entropy or wall-clock state while parsing, matching, validating, hashing, or publishing data.

This contract scans every Python module below `src/haxlab/ingestion/` and rejects calls that acquire nondeterministic process-external state:

- `random.*` and `secrets.*`;
- `uuid.uuid1()` and `uuid.uuid4()`;
- wall-clock and runtime clocks such as `time.time()`, `time.monotonic()`, `time.perf_counter()`, and related nanosecond/process/thread variants;
- `datetime.datetime.now()`, `utcnow()`, `today()`, and `datetime.date.today()`.

The scanner resolves ordinary import aliases, assignment aliases, and constant-string `getattr(...)` aliases so simple indirection cannot bypass the boundary.

Deterministic operations remain allowed. Examples include parsing an explicit timestamp with `datetime.fromisoformat`, using `uuid.uuid5` with explicit inputs, sleeping in operational glue, hashing bytes, reading filesystem metadata, and consuming timestamps already present in source evidence.

This lane is intentionally separate from network/process hermeticity and process-state mutation checks. It does not change ingestion product code; it only prevents future ingestion changes from silently introducing ambient nondeterminism.

Acceptance requires the focused contract and repository test collection to pass on the exact live branch head on the self-hosted HaxLab runner.
