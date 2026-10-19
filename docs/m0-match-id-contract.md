# Canonical M0 replay/report identity — fail closed before publication

`run_import()` publishes derived `matches.jsonl` that downstream consumers address by `match_id`. A distinct replay can share a Discord-derived report ID with another match; before this fix the producer could emit **duplicate canonical identities** for different source rows.

## Source-bound identity

- If the matched source report has a non-empty canonical `report_id`, that ID remains the M0 identity.
- Without a report ID, the existing `hbr2:<first-16-lowercase-sha256>` fallback remains unchanged.
- The replay SHA-256 must be a full lowercase 64-character digest; ID and source message strings must be non-empty and trimmed, rather than silently coerced.
- The code validates the **entire** derived canonical record collection before creating output directories or publishing any of the M0 files.
- Any duplicate `match_id`, including collisions between different report evidence or truncated SHA prefixes, fails closed with `DuplicateMatchIdError` identifying each colliding canonical ID and its sorted `(replay_sha256, source_message_id)` provenance. Error evidence is stable under source reordering.
- No arbitrary suffix is added to repair collisions; the upstream source evidence must be corrected. Successful unique record order and existing report/fallback naming stay unchanged.

## Scope and delivery

Reuses the source-only canonical helper from historical draft HaxLab PR #275 and connects it to the actual M0 ingestion producer. No historical QA suite or workflow is imported, created or manually run. The modification does not rewrite existing output on rejection, remove protected source evidence, or change Discord parsing, matching scores, HaxLab VPS/runtime, model training, promotions or claims.

This is not an authorization for import, archive publication or deployment. The existing exact-head CI, independent review and protected release boundaries are still mandatory.
