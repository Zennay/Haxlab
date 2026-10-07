# Discord import failure provenance contract

Issue #89 tracks a reproducibility gap in M0 import evidence: Discord JSON/message
failures currently embed the absolute export path in `ImportFailure.source`, so two
byte-identical exports mounted at different roots can produce different
`manifest.json` bytes.

This isolated lane stages a pure provenance helper without modifying the active
`discord_export.py` worker-owned surface.

## Contract

- a failure source is always relative to the supplied immutable export root;
- lexical `.` / `..` aliases are normalized before containment is checked, so equivalent path spellings yield the same provenance while parent escapes fail closed;
- path separators are rendered with POSIX `/` semantics so the derived evidence is
  stable across host mount locations;
- a source outside the export root fails closed instead of leaking an absolute path;
- the export root itself is not a valid file provenance;
- optional per-message provenance uses the exact suffix `#message:<id>`;
- message suffix tokens must be non-empty, already canonical strings and cannot
  contain `#` or ASCII control characters, including DEL (`0x7f`);
- `unknown` remains valid so the current per-message isolation lane can preserve its
  fallback when a malformed message has no usable Discord message id.

## Reproducibility proof

`tests/test_failure_provenance.py` constructs equivalent failure evidence under two
different absolute roots and serializes a manifest-shaped payload using the same
sorted/indented JSON convention as the importer. The resulting bytes must be
identical and contain neither absolute root.

## Intended integration

The active Discord-report lane should call `canonical_failure_source()` at each
`ImportFailure(source=...)` boundary instead of `str(path)` or
`f"{path}#message:{message_id}"`. Integration is complete only after an end-to-end
`run_import()` regression injects equivalent malformed Discord exports at two roots
and proves byte-identical real `manifest.json` output. Until that producer owner
performs the wiring, this branch remains an isolated contract lane.
