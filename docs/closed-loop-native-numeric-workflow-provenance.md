# Closed-loop native-numeric workflow provenance

The native-numeric validation workflow contributes evidence about the closed-loop Arena gate. That evidence is valid only when both the checkout implementation and checked-out HaxLab source are immutable and proven before tests execute.

## Invariants

`.github/workflows/closed-loop-native-numeric-validation.yml` must:

1. pin official GitHub Actions to immutable 40-character commit SHAs;
2. use reviewed `actions/checkout` v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
4. fail closed unless `git rev-parse HEAD == github.sha` before creating the test environment;
5. preserve read-only permissions, the `[self-hosted, haxlab]` runner, the existing timeout, historical branch trigger, and the existing Arena/duel/multisource regression scope.

## Rationale

A moving `actions/checkout@v4` tag can change between reruns of the same HaxLab commit. Persisted checkout credentials also leave an unnecessary repository token in git configuration for later validation steps. Printing HEAD only after regressions is not an acceptance boundary.

Pinning the action, disabling persisted credentials, and asserting exact HEAD immediately after checkout make provenance a prerequisite for evidence generation without changing Arena logic, numeric evidence semantics, thresholds, models, or champion state.

## Proof sequencing

`.github/workflows/closed-loop-native-numeric-provenance-proof.yml` is manual-only. It requires both dispatched-SHA identity and live remote branch-head identity before running the focused provenance contract plus the adjacent closed-loop regressions.

Do not dispatch it while `#100 → #436` owns the shared HaxLab runner. A later green proof is narrow workflow-provenance evidence only.
