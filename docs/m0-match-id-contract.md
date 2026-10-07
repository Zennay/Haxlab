# Canonical M0 match identity contract

Issue #90 tracks a producer-side integrity gap: two distinct Discord reports can carry
the same parsed `report_id`, causing two otherwise distinct replay/report pairs to
derive the same canonical `match_id`.

This lane adds a side-effect-free pre-publication validator:

- every candidate canonical row must have a native, non-empty, whitespace-canonical
  `match_id`;
- every candidate must carry the full replay SHA-256 as exactly 64 lowercase hex characters; optional message provenance must be either `None` or a native canonical string;
- duplicate `match_id` values fail closed and report the complete provenance set for
  every duplicate identity;
- duplicate diagnostics are canonicalized by `match_id` and provenance, so the same
  conflicting source set produces exactly the same failure evidence regardless of
  source ordering;
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

## Deterministic failure semantics

Validation consumes the complete candidate set before deciding whether duplicate
identities exist. Duplicate groups are then rendered in canonical `match_id` order,
with each group's `(replay_sha256, source_message_id)` provenances sorted
lexicographically. This avoids an otherwise subtle observability drift where two
reruns over identical evidence could emit different failure text solely because source
discovery order changed.


## Provenance boundary

The producer currently emits a full lowercase SHA-256 for every canonical match row, and
the independent cross-artifact auditor already treats any other replay hash form as
invalid. The pre-publication validator enforces the same boundary before identity
uniqueness is evaluated. This guarantees that duplicate diagnostics always identify
the complete replay provenance, including the rare fallback-ID collision case where
two full hashes share the same `hbr2:<16-char-prefix>`.


## Machine-readable conflict evidence

`DuplicateMatchIdError.conflicts` exposes the exact canonical conflict set as immutable
tuples. The error boundary itself sorts match IDs and each provenance set, so callers
receive deterministic structured evidence even if they construct or forward conflicts
in a different source order. Human-readable exception text is derived from that same
canonical structure; logs and tests therefore cannot disagree about ordering.


## Canonical derivation helper

`derive_canonical_match_id()` freezes the producer's existing identity rule without
touching the active producer surface: a canonical non-empty `report_id` is used
verbatim when present; otherwise the identity is exactly
`hbr2:<first 16 lowercase hex characters of replay_sha256>`. The helper rejects
malformed report IDs and replay hashes instead of coercing them. Producer integration
can therefore reuse the same derivation and uniqueness boundary rather than
re-implementing fallback semantics in two places.
