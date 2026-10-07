# Calibration-summary validation action provenance

The calibration-summary validation workflow is both an evaluation proof and a write-capable evidence publisher: after exact/live-head validation and calibration regressions it posts a receipt to GitHub issue #375 with \`issues: write\`. Its official action implementations therefore need immutable provenance in addition to the already existing source-SHA checks.

## Invariants

\`.github/workflows/calibration-summary-pythonpath-validation.yml\` must:

1. pin \`actions/checkout\` to reviewed v4 commit \`11d5960a326750d5838078e36cf38b85af677262\`;
2. pin \`actions/github-script\` v7 to resolved immutable commit \`f28e40c7f34bde8b3046d885e986cb6290c5673b\`;
3. disable persisted checkout credentials while keeping exact \`github.sha\` and \`clean: true\`;
4. preserve the existing pre-proof local HEAD and remote branch-head equality checks;
5. preserve \`contents: read\` plus \`issues: write\`, the self-hosted HaxLab runner, 20-minute timeout, historical/manual trigger, calibration regression set, and issue #375 receipt boundary.

## Rationale

Exact source identity alone does not make a rerun reproducible when the workflow resolves mutable action tags. This matters especially for \`actions/github-script\`, because that action receives the workflow token and publishes the acceptance receipt. Pinning both official actions closes that mutable execution surface without changing calibration behavior or evidence semantics.

Issue #375's original summary-import fix is already complete. This follow-up only hardens the validator that proves that fix. It does not modify the canonical calibration workflow, reuse logic, thresholds, frozen inputs, models, champion state, or promotion state.

## Proof sequencing

\`.github/workflows/calibration-summary-action-provenance-proof.yml\` is manual-only. It binds the dispatched SHA to both checked-out HEAD and live branch HEAD, validates this provenance contract in an isolated environment, then runs the existing adjacent calibration regressions. It intentionally does not publish a receipt or mutate GitHub state.

Keep it undispatched while the higher-priority canonical evaluation sequence owns the shared HaxLab runner.
