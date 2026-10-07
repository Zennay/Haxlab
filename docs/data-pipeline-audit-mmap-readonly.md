# Data-pipeline memory-map read-only contract

HaxLab data-pipeline auditors judge existing evidence and must not modify the bytes they verify. File-backed Python `mmap` mappings can mutate evidence without using ordinary `write(...)` APIs, so the normal filesystem mutation checks are not sufficient on their own.

This additive contract recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

## Required mapping mode

A data-pipeline auditor may create a memory map only when the call uses an explicit keyword `access=` whose value is statically proven to be:

- `mmap.ACCESS_READ`, or
- `mmap.ACCESS_COPY`.

`ACCESS_COPY` permits private copy-on-write changes without modifying the backing evidence.

The contract rejects omitted or dynamic access modes, `ACCESS_WRITE`, `ACCESS_DEFAULT`, and calls that mix an otherwise safe `access=` value with explicit `prot=` or `flags=` overrides. Requiring one portable, explicit mode avoids platform-dependent Unix/Windows constructor defaults.

Import aliases, constructor aliases, constant aliases and constant `getattr(...)` aliases are resolved before the decision.

## Ownership separation

Issue #483 owns only file-backed memory-map mutation safety for data-pipeline audit modules. It does not modify any auditor or producer implementation and remains separate from the general read-only contract and the native-FFI lane #478.

## Validation ownership

The mmap lane may enter PR validation only after native-FFI lane #482 has integrated, keeping the active data-pipeline audit validation work serialized.
