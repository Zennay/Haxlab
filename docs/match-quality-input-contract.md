# Match-quality evidence input boundary

HaxLab ranks matches using `assess_match_quality(evidence)`. Before the normal threshold/scoring logic runs, callers must pass the **exact native** `MatchQualityEvidence` data object.

Unexpected objects, dictionaries, forged subclasses or other values are returned as a deterministic **REJECTED** assessment: `weight=0`, `reasons=("invalid_quality_evidence",)`, no invented missing evidence. The scorer does not read arbitrary caller-controlled object attributes first.

For numeric fields, unrepresentably large native integers are treated as invalid evidence rather than leaking an `OverflowError`. Actual `MatchQualityEvidence` values continue through the existing field-specific validation and unchanged conservative scoring thresholds. The result is still a standard `MatchQualityAssessment`. It does not authorize model promotion, learning, deployment, or acceptance of questionable replay provenance.

Product source integrated from historical independent HaxLab issue #418 / PR-less `feature/data-pipeline-quality-evidence-type-20261007` onto the new current-main branch. Historical QA sources and worker/claim state are untouched. No new QA files or manually triggered tests; existing CI/review and release conditions remain required.
