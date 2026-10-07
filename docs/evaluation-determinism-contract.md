# Evaluation determinism contract

HaxLab promotion and Arena decisions must be reproducible from explicit evidence, policy, scenario data, model bytes and explicit seeds. A process restart, different `PYTHONHASHSEED`, wall-clock time, host environment variable, UUID source or OS entropy must not silently change a gate decision.

## Contract

Production Python code under `src/haxlab/evaluation/` is recursively checked for ambient nondeterminism.

The regression rejects:

- Python's randomized builtin `hash()`;
- reads from `os.environ` or `os.getenv()`;
- wall-clock reads through `time.time()` / `time.time_ns()` or `datetime.now()` / `utcnow()` / `date.today()`;
- `uuid1()` / `uuid4()`;
- `secrets` entropy APIs;
- module-global `random` APIs and unseeded `random.Random()`;
- wildcard imports, because they make the static provenance check ambiguous.

Explicit deterministic seed values passed through HaxLab's evaluation APIs remain allowed. A locally constructed `random.Random(explicit_seed)` may be introduced later only with review and regression coverage proving the seed is explicit evidence.

This is a validation contract only. It does not alter evaluation thresholds, source suites, models, champion state or Arena runtime behavior.

## Integration

The lane is stacked directly on the canonical Arena-v2 head and remains separate from schema, policy-config, evidence-I/O, resync, calibration, promotion and cross-workflow owners. Exact-head self-hosted proof is required before integration.
