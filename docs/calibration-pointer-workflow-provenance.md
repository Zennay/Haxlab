# Calibration pointer workflow provenance contract

The calibration-pointer invariant workflow reads live champion pointers, model artifacts, and the frozen evaluation input set. Those observations are meaningful only when the repository source producing the evidence is itself immutable and verified first.

## Invariants

`.github/workflows/calibration-pointer-invariant-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before any live champion/model/frozen-input path is read;
5. preserve read-only permissions, `[self-hosted, haxlab]`, timeout, triggers, and the existing stable-input/hash checks.

## Rationale

The workflow snapshots live provenance inputs twice and reports exact-head evidence. A mutable checkout action or an unverified worktree would allow those live observations to be associated with the wrong source implementation. Persisted checkout credentials are unnecessary for the read-only validation job.

This contract changes no pointer, model, frozen input, threshold, champion state, or calibration decision. It only strengthens source provenance before the existing read-only observations begin.

## Proof sequencing

`.github/workflows/calibration-pointer-workflow-provenance-proof.yml` is manual-only. It validates the workflow source contract on an exact dispatched and live branch head without touching live calibration state.

Keep the proof undispatched while canonical `#100 → #436` sequencing remains owner-held. The canonical pointer workflow itself remains the authority for live-input evidence once this source hardening is eligible to integrate.
