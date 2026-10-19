# Canonical match-quality assessment admission

`MatchQualityAssessment` is the public result of replay-quality scoring. Downstream weighting and selection must never receive malformed or ambiguous assessment state.

- `tier` is exactly a member of the native `QualityTier` enum; strings and spoofed subclass values are not accepted as substitutes.
- `weight` must be a native Python `int` or `float`, finite and between **0 and 1** inclusive. Boolean weights are explicitly rejected.
- `reasons` and `missing_evidence` must be tuples of unique non-empty native strings. Mutable containers and duplicate identifiers are rejected.
- An `ELITE` assessment cannot simultaneously declare missing evidence.
- Invalid construction raises `ValueError` rather than circulating into scoring consumers. Existing valid quality-scoring behavior and threshold policy are unchanged.

This fix applies to the canonical result dataclass only, not the raw evidence schema or scoring algorithm. It is a fresh-current-main **product source integration** of historical draft #321, with that draft left untouched. No test/QA source was copied, created, extended or manually run, and no data ingestion, runtime, models or champion promotion were changed. Existing CI/review and release approvals remain required before merge and deployment.
