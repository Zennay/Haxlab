# Evaluation explicit-path contract

HaxLab evaluation gates must derive decisions from explicit evidence and configuration inputs.

Production modules under `src/haxlab/evaluation/` may read an explicit path supplied by a caller or a validated contract. They must not discover additional candidate inputs by enumerating ambient directory contents.

The validation contract rejects:

- `Path.glob(...)`, `Path.rglob(...)`, `Path.iterdir()`, and `Path.walk()`;
- `os.walk(...)`, `os.fwalk(...)`, `os.listdir(...)`, and `os.scandir(...)`;
- `glob.glob(...)` and `glob.iglob(...)`;
- direct import aliases, simple/annotated/named-expression aliases, constant-`getattr(...)` aliases, and unbound `Path.glob(path, ...)` / `Path.rglob(path, ...)` style calls.

The scanner resolves actual `pathlib.Path` instances before classifying path discovery methods. Unrelated application objects that happen to expose a method named `walk`, `glob`, or similar are not rejected.

Why this matters: an immutable Git SHA is not sufficient evidence if the gate can silently consume files that happen to be present in a mutable self-hosted runner workspace. Evaluation inputs should be explicitly named, provenance-bound, and validated by their owning contracts.

This contract does not prohibit reading, hashing, statting, or opening an explicit evidence path. It is additive validation only and does not change Arena thresholds, model/champion state, calibration inputs, promotion behavior, or canonical workflow sequencing.
