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
numbers in `[0, 1]`, and the config must be a regular non-symlink file. The
loader opens the file descriptor with `O_NOFOLLOW` and verifies the opened
object with `fstat` before reading bytes, so a path cannot pass a symlink check
and then be swapped to a symlink before the read. Platforms without a no-follow
open primitive fail closed rather than silently weakening this provenance
boundary. Receipt
v1 also requires the runtime `PromotionPolicy` to remain a **frozen** dataclass
and to expose exactly the four bound fields with their current runtime types
(`int`, `float`, `float`, `bool`); a future mutable, extra/defaulted, or retyped
policy fails closed instead of weakening the provenance contract or accepting a
loader/runtime mismatch.

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
rejects runtime policy-schema drift, proves an explicitly loaded policy changes the existing `decide_promotion` gate at the configured threshold, and covers the fail-closed input boundary.

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


## Bounded input

Policy configuration is a small control-plane artifact. The loader fails closed before TOML parsing when the opened regular file exceeds 64 KiB, and its read call is capped to one byte beyond that limit so a misleading or racing size report cannot cause an unbounded allocation. The bound is part of the evaluation-validation boundary; larger files must not be treated as valid promotion policy input.


## Special-file liveness

The secure open uses both no-follow and non-blocking flags before inspecting the opened descriptor. This is required because a FIFO can otherwise block at open time before the regular-file check runs. After opening, the descriptor must still pass the regular-file `fstat` check; FIFOs, sockets, devices, directories and other special files are invalid policy inputs.


## Read-time mutation

The opened descriptor is fingerprinted with device, inode, byte size, modification time and change time before the bounded read, then checked again afterwards. The byte count must also equal the final descriptor size. Any drift fails closed instead of parsing a potentially torn policy snapshot. This complements no-follow path safety: path replacement and in-place mutation are separate hazards.
