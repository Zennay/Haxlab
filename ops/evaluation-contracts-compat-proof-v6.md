# Evaluation additive-contract compatibility proof v6

Proof-only carrier for issue #283.

Canonical Arena-v2 base:
- `ee5ab508e9d8504de12b7cbb5fdeca43a58ae1a4`

This carrier combines the 18 additive validation contracts proven together before #281 with the exact native-FFI contract from #281:
- native FFI guard: `19b4d05c4e47a2cff7ae83bbf156efa3731dc520`

The workflow:
1. checks out the exact untouched canonical Arena-v2 commit;
2. verifies all 19 owner branches still resolve to their recorded exact green SHAs;
3. materializes only their additive contract test/workflow files into the runner workspace;
4. compiles and runs the full 19-contract synthetic stack;
5. runs the adjacent canonical Arena-v2 regression bundle;
6. emits `HAXLAB_EVALUATION_CONTRACT_STACK_COMPAT_V6=green` only after complete success.

The still-unaccepted #264 nested-regression product hardening is deliberately excluded from this clean additive-contract proof. #92 remains owner-reserved and its known runtime-budget test is not collected.

No canonical branch, owner branch, evaluation implementation, threshold, model, scenario source, calibration input, champion pointer, resync state, or promotion state is modified. Close the proof PR without merge after terminal green exact-head evidence is recorded.
