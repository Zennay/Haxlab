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
numbers in `[0, 1]`, and the config must be a regular non-symlink file.

## Validation

Validate and render the mapped runtime policy without changing champion state:

```bash
python -m haxlab.evaluation.policy_config configs/autonomy.toml
```

Successful output is deterministic compact JSON. Any malformed policy config
exits non-zero through the CLI parser.

`tests/test_evaluation_policy_config.py` binds the repository's canonical
`configs/autonomy.toml` values to the current `PromotionPolicy` defaults and
covers the fail-closed input boundary.

## Runtime wiring status

The strict parsing/mapping boundary is implemented, but existing promotion
orchestration does not yet load this TOML automatically. Until that integration
is completed, the runtime's default `PromotionPolicy` remains authoritative.

GitHub issue #93 tracks the wiring step. It should be integrated only after the
active promotion/generation-loop ownership clears, with an exact-head regression
proving the loaded policy is the object actually passed into the promotion
decision. Do not infer from the presence of `configs/autonomy.toml` alone that
editing the file currently changes a running promotion gate.
