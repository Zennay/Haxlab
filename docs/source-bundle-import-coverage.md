# Source-bundle importer coverage contract

HaxLab's raw-source provenance is only complete when the source-bundle receipt covers exactly the bytes that the ingestion entry points are willing to consume.

The integrated receipt and verifier establish exact-byte identity for their own inventory. This contract protects the separate assumption that their inventory stays aligned with the real importer selectors.

## Protected boundary

The regression exercises the public ingestion entry points against one synthetic raw-export tree:

- `discover_replays()` for HBR2 replay discovery;
- `read_discord_exports()` for Discord JSON discovery;
- `create_source_bundle_receipt()` for provenance inventory.

The fixture deliberately contains both accepted inputs and near-miss extensions.

Current accepted raw-source selection is:

- HBR2 suffix, case-insensitive;
- Discord JSON with lowercase `.json`.

Current deliberate near misses include uppercase `.JSON`, `.jsonl`, `.hbr`, `.hbr2.gz`, and unrelated text files.

## Invariant

The exact `(kind, root-relative path)` set observed through the two importer entry points must equal the exact source-receipt inventory.

That protects both directions:

1. an importer may not start consuming a raw file that source provenance omits;
2. the receipt may not claim raw inputs that neither importer consumes.

If source selection intentionally changes, importer behavior and source provenance must be changed together and this contract updated in the same reviewed integration.

## Scope

This is a validation-only contract. It does not modify discovery, Discord parsing, source receipt generation, persisted receipt verification, M0 publication, runtime state, learning datasets, evaluation gates, models, or champion state.
