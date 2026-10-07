# M0 generation commit and pointer contract

Issue #91 requires generation-level publication semantics: readers must never mistake
a partially replaced five-file M0 dataset for one committed generation.

This lane is stacked on PR #95's exact-byte dataset receipt. It deliberately does not
modify `pipeline.py`, the existing per-file atomic-publication branches or filesystem
publication behavior. Instead it freezes the metadata contract needed by the eventual
generation-directory + final-pointer swap.

## Generation commit

`build_generation_commit()` accepts only a strict API-form
`haxlab-m0-dataset-receipt-v1` receipt. It recomputes the receipt digest before any
generation metadata is accepted, then produces:

- schema `haxlab-m0-generation-commit-v1`;
- content-addressed `generation_id = m0-<receipt_sha256>`;
- the exact receipt schema and receipt SHA-256;
- all five canonical artifact records in canonical M0 order, including exact
  `size_bytes` and SHA-256;
- a `commit_sha256` over canonical compact JSON for the complete commit core.

The persisted form from `generation_commit_bytes()` is canonical UTF-8 JSON plus one
trailing newline. Relocating identical artifact bytes produces exactly the same
receipt, generation ID, commit payload and commit digest.

## Final pointer

`build_generation_pointer()` accepts only a self-consistent generation commit and
emits the minimal metadata intended for the final atomic publication boundary:

- schema `haxlab-m0-generation-pointer-v1`;
- `generation_id`;
- `receipt_sha256`;
- `commit_sha256`.

`generation_pointer_bytes()` is deterministic canonical JSON plus one newline. The
eventual producer can fsync a complete immutable generation directory and its commit
metadata first, then atomically replace only this small pointer. A reader that trusts
the pointer can resolve one content-addressed generation and validate its commit and
receipt rather than opening five mutable top-level files independently.

## Canonical layout

The contract fixes one relative filesystem layout:

- generation directories live at `generations/<generation_id>/`;
- each immutable generation stores its commit at
  `generations/<generation_id>/generation-commit.json`;
- the only mutable publication selector is the top-level
  `current-generation.json` pointer.

`generation_directory()` and `generation_commit_path()` derive those paths only from
a fully validated commit, so a malformed generation ID cannot become a path traversal
or alternate layout.

## Reader contract

`parse_generation_commit_bytes()` and `parse_generation_pointer_bytes()` accept only
bounded native byte payloads, strict UTF-8 JSON objects, no duplicate JSON keys and the
exact canonical serialization (including one trailing newline). The commit reader
recomputes both the embedded receipt digest and the generation commit digest.

`validate_generation_pointer(pointer, commit)` then requires the reader-visible
pointer to equal the pointer derived from the exact validated commit. A stale pointer,
cross-generation commit or tampered digest therefore fails closed before artifact
consumption.

## Fail-closed semantics

The contract rejects:

- wrong/extra receipt fields or receipt schema drift;
- non-native or wrong artifact counts;
- missing, reordered or renamed M0 artifact records;
- non-native/negative sizes or malformed SHA-256 values;
- receipt evidence whose claimed digest does not recompute exactly;
- generation IDs that do not match the bound receipt;
- generation commits whose `commit_sha256` does not recompute exactly;
- oversized/non-byte/non-UTF-8/noncanonical persisted evidence;
- duplicate JSON keys in commit or pointer evidence;
- pointers that do not bind the exact generation commit supplied by the reader.

## What remains for Issue #91

This branch defines identity and commit/pointer bytes only. It does **not** claim the
crash-safety acceptance criteria are complete. The producer owner still needs to:

1. stage all five files into a new generation directory;
2. fsync each artifact and the generation directory;
3. write/fsync the canonical generation commit;
4. make the complete generation directory visible without overwriting an existing
   committed generation;
5. atomically replace the current-generation pointer only after steps 1-4 are durable;
6. update readers to resolve the pointer and validate commit + receipt;
7. inject failures at every publication boundary and prove readers observe either the
   previous complete generation or the new complete generation, never a mixed set.

Those producer/consumer filesystem changes remain separate from this pure contract
lane so the existing `pipeline.py` / atomic-publication owners are not duplicated.
