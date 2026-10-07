# Evaluation policy configuration contract

`configs/autonomy.toml` contains the operator-facing promotion thresholds under
`[evaluation]`. The strict parser for that surface lives in
`haxlab.evaluation.policy_config`.

## Canonical mapping

| autonomy.toml key | PromotionPolicy field |
| --- | --- |
| `minimum_games_vs_champion` | `minimum_games` |
| `minimum_score_rate_lower_bound` | `minimum_score_rate_lower_bound` |
| `minimum_frozen_scenario_pass_rate` | `minimum_scenario_pass_rate` |

`allow_critical_regressions` is intentionally not configurable through
`autonomy.toml`; the loader fixes it to `False`.

The `[evaluation]` table is an exact contract. Missing or unknown keys are
rejected. Counts must be native integers, probabilities must be native finite
numbers in `[0, 1]`, and the config must be a regular non-symlink file. Receipt
v1 also requires the runtime `PromotionPolicy` dataclass to expose exactly the
four bound fields; a future extra/defaulted field fails closed instead of being
silently omitted from the config contract.

## Immutable source provenance

`load_promotion_policy_config()` reads the config bytes once and returns the
validated `PromotionPolicy` together with receipt schema
`haxlab-promotion-policy-config-v1`, the exact source SHA-256, and byte size.
This distinguishes two files that happen to map to the same policy values but do
not have identical bytes.

Promotion evidence can therefore bind a future runtime decision to the exact
configuration artifact and receipt contract that produced it instead of
recording only normalized threshold values.

## Validation

Validate and render the mapped runtime policy without changing champion state:

```bash
python -m haxlab.evaluation.policy_config configs/autonomy.toml
```

Successful output is deterministic compact JSON with `schema`, `policy`, and
`source` fields. The source object contains `sha256` and `size_bytes`.
Any malformed policy config exits non-zero through the CLI parser.

`tests/test_evaluation_policy_config.py` binds the repository's canonical
`configs/autonomy.toml` values to the current `PromotionPolicy` defaults,
binds provenance to the exact file bytes, proves semantically equal byte-drift
changes the provenance hash, locks the versioned deterministic JSON receipt,
rejects runtime policy-schema drift, and covers the fail-closed input boundary.

## Runtime wiring status

The strict parsing, mapping, source-provenance, and receipt-schema boundary is
implemented, but existing promotion orchestration does not yet load this TOML
automatically. Until that integration is completed, the runtime's default
`PromotionPolicy` remains authoritative.

GitHub issue #93 tracks the wiring step. It should be integrated only after the
active promotion/generation-loop ownership clears, with an exact-head regression
proving the loaded policy is the object actually passed into the promotion
decision. Do not infer from the presence of `configs/autonomy.toml` alone that
editing the file currently changes a running promotion gate.
