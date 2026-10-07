# Closed-loop native-numeric workflow provenance contract

The closed-loop native-numeric validator produces evaluation evidence and labels that evidence exact-head. Its checkout must therefore be immutable and verified before any evaluation regressions execute.

The workflow pins `actions/checkout` to reviewed immutable v4 commit `11d5960a326750d5838078e36cf38b85af677262`, keeps `ref: ${{ github.sha }}` and `clean: true`, disables persisted checkout credentials, and fails immediately after checkout unless `git rev-parse HEAD` equals the immutable GitHub event SHA.

A focused provenance contract runs before the existing closed-loop, duel-gate, and multisource-duel regressions. This staging branch deliberately is not added to the push trigger, so preparing the contract does not add another self-hosted HaxLab job while higher-priority evaluation validation is queued.

This lane changes validation provenance only. It does not change Arena metrics, threshold values, policy semantics, models, champion pointers, promotion state, calibration inputs, or the canonical Arena integration workflow. Exact-head self-hosted proof is still required before this lane can advance to PR/review.
