# Match-quality input contract

`assess_match_quality()` is a data-pipeline evidence boundary. It must not read
fields from arbitrary objects before proving that the input is the canonical
`MatchQualityEvidence` value type.

## Contract

- Only an exact `MatchQualityEvidence` enters field-level quality scoring.
- Wrong object types and subclasses fail closed as a deterministic rejected
  assessment with weight `0.0` and reason `invalid_quality_evidence`.
- Rejection happens before evidence attributes are read.
- Existing field validation, thresholds, weights, tier boundaries and missing
  evidence behavior remain unchanged for canonical inputs.

Exact type validation is intentional here: a subclass could override attribute
access and turn an evidence read into call-history or arbitrary runtime behavior.

## Scope

This contract changes `quality/scoring.py` plus its focused scoring regressions.
It does not change the quality model definitions owned separately by PR #321,
nor ingestion, runtime, learning, evaluation, champion or live state.
