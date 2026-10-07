# Data-pipeline audit safe-deserialization contract

HaxLab audit modules parse evidence that can be malformed or adversarial. They must stay on data-only parsing surfaces and never interpret evidence as executable Python objects, code, or model payloads.

The validation contract recursively covers every `*_audit.py` under ingestion, learning, and runtime.

## Rejected surfaces

The contract rejects imports from executable/object serialization stacks such as `pickle`, `marshal`, `shelve`, `dill`, `cloudpickle`, and `joblib`. It also rejects:

- `eval`, `exec`, and dynamic `compile`;
- unsafe/full PyYAML loading;
- Torch and TorchScript object loading;
- pandas pickle loading;
- NumPy loads that explicitly enable pickle or make the `allow_pickle` choice dynamic.

Aliased imports are resolved before checking, so renaming a loader does not bypass the contract.

## Allowed data-only parsing

Auditors may continue to use JSON, TOML, CSV-style parsing, `yaml.safe_load` / `safe_load_all`, `ast.literal_eval`, and NumPy loading with its safe default or explicit `allow_pickle=False`.

## Ownership separation

This lane only guards executable/object deserialization. Import hermeticity, ambient nondeterminism, execution-time mutation, and broad exception handling remain independently owned by their existing validation lanes.

No audit implementation, producer, runtime state, evaluation code, model, or champion state is modified.
