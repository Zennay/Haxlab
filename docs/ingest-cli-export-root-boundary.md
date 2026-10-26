# Ingest CLI export-root boundary

The positional `export_root` accepted by `haxlab ingest` is the immutable raw-input boundary.

The CLI requires the supplied path to name an existing directory without filesystem symlink indirection. It rejects both:

- an export root whose final path entry is itself a symlink;
- a real export directory reached through a symlinked ancestor.

The ancestor check compares the lexical absolute path with the strict filesystem-resolved path before `run_import()` is called. This preserves normal absolute and relative directory inputs while failing closed when a symlink redirects any component of the raw source path.

This validation is intentionally separate from the derived-output boundary enforced by `run_import()`; it does not modify ingestion, matching, replay validation, or output publication semantics.
