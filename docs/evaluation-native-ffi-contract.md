# Evaluation native FFI boundary contract

HaxLab evaluation gates must remain inside the reviewable Python/runtime boundary
and must not gain an explicit escape hatch into arbitrary process-native code.

## Required invariant

Production modules recursively below `src/haxlab/evaluation/` must not:

- import `ctypes`, `_ctypes`, `cffi`, or `numpy.ctypeslib`;
- call `numpy.ctypeslib.load_library`;
- call `torch.ops.load_library` or `torch.classes.load_library`;
- compile/load native extensions through
  `torch.utils.cpp_extension.load` or `load_inline`;
- call `tensorflow.load_op_library`.

The detector resolves ordinary import aliases, direct symbol imports, simple
assignment aliases, and constant `getattr(...)` spellings so a native loader
cannot be hidden behind a local name.

Normal numeric/model imports and operations remain allowed. This is not a ban on
native-backed Python packages such as NumPy or PyTorch; it is a ban on evaluation
code explicitly opening a dynamic-library/FFI execution channel.

## Why this is distinct

The dynamic-loading contract (#170) blocks runtime loading of Python modules and
loader objects such as `importlib.machinery.ExtensionFileLoader`, but an ordinary
static `import ctypes` or `import cffi` does not violate that rule.

The hermeticity contract (#151) blocks network access, shell execution and
unapproved subprocesses, but native FFI executes inside the current process and
does not require a child process.

The executable-deserialization contract (#155), process-state contract (#200),
and mutable control-plane import contract (#159) protect different boundaries.
None of them rejects explicit native-library loading.

## Proof

`tests/test_evaluation_native_ffi_contract.py` recursively parses the complete
evaluation package. Focused regressions cover direct/native imports, NumPy,
PyTorch and TensorFlow loader helpers, assignment aliases and constant
`getattr(...)` resolution, while allowing normal non-loader numeric/model code.

The branch-scoped self-hosted proof compiles the evaluation package and contract,
runs the focused test, then runs the adjacent canonical Arena-v2 evaluation
regressions before emitting `HAXLAB_EVALUATION_NATIVE_FFI_RESULT=green`.

A green result proves only this narrow invariant. It does not authorize canonical
Arena-v2 PR #19 integration, resync, or champion promotion.
