# Data-pipeline native FFI hermeticity contract

HaxLab data-pipeline auditors are evidence verifiers. Their behavior must remain reviewable from Python source and explicit local evidence. Loading native libraries at audit runtime would create an escape around the existing Python import, subprocess, network and read-only contracts because arbitrary native code could be selected from ambient filesystem state.

This additive contract recursively covers every `*_audit.py` module under:

- `src/haxlab/ingestion/`;
- `src/haxlab/learning/`;
- `src/haxlab/runtime/`.

## Forbidden surfaces

The contract rejects:

- imports of `_ctypes`, `ctypes` or `cffi`;
- `ctypes.CDLL(...)`, `ctypes.PyDLL(...)`, `ctypes.WinDLL(...)` and `ctypes.OleDLL(...)`;
- `ctypes.cdll.LoadLibrary(...)` and `ctypes.pydll.LoadLibrary(...)`;
- `_ctypes.dlopen(...)`, `cffi.FFI().dlopen(...)` and other `dlopen(...)` call shapes;
- higher-level native loaders such as `numpy.ctypeslib.load_library(...)` and `torch.ops.load_library(...)`;
- simple assignment aliases and constant-`getattr(...)` aliases of the loading APIs.

## Allowed behavior

Auditors may continue to parse local files, hash evidence, validate JSON/SQLite state and perform deterministic pure-Python computation. This contract does not modify any production auditor or producer.

## Ownership separation

Issue #478 owns only native FFI/library loading in data-pipeline audit modules. The evaluation-native-FFI lane is scoped to `src/haxlab/evaluation` and does not overlap. Issue #474 independently owns SQLite extension loading.

PR promotion is allowed only after the SQLite-extension lane #477 has integrated, keeping validation ownership serialized and file-disjoint.
