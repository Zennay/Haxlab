# Canonical M0 match identity contract

Issue #90 tracks a producer-side integrity gap: two distinct Discord reports can carry
the same parsed `report_id`, causing two otherwise distinct replay/report pairs to
derive the same canonical `match_id`.

This lane adds a side-effect-free pre-publication validator:

- every candidate canonical row must have a native, non-empty, whitespace-canonical
  `match_id`;
- optional replay/message provenance must also be native canonical strings;
- duplicate `match_id` values fail closed and report both source provenances;
- successful validation returns the complete identity set in deterministic sorted
  order, independent of source ordering.

The helper deliberately does **not** modify `pipeline.py` while that producer surface
is owned by another active data-pipeline lane. The intended integration point is after
the complete `canonical_matches` list has been built and before any M0 artifact is
written. That preserves the current `hbr2:<sha-prefix>` fallback behavior while
preventing duplicate canonical identities from reaching `matches.jsonl`.

Integration is only complete once the producer owner wires this validator into that
pre-publication boundary and the end-to-end duplicate-`report_id` fixture proves that
no M0 artifact set containing duplicate canonical match IDs can be published.
