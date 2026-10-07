# Duel-policy workflow provenance contract

The duel-policy integrity workflow is evaluation evidence, so its own execution provenance must be reproducible before its test results are trusted.

The workflow now pins `actions/checkout` to the reviewed immutable v4 commit `11d5960a326750d5838078e36cf38b85af677262`, checks out the immutable GitHub event SHA, disables persisted checkout credentials, and fails before environment setup unless `git rev-parse HEAD` exactly equals `github.sha`.

The focused regression contract runs before the duel-policy and adjacent evaluation suites. It protects the immutable checkout ref, event-SHA binding, explicit post-checkout identity assertion, and the proof trigger for the provenance-hardening branch.

This lane changes validation provenance only. It does not change duel thresholds, duel evidence semantics, multisource decisions, model files, champion pointers, promotion state, or the canonical Arena integration workflow. A green workflow run is narrow evidence for this workflow head and does not authorize Arena merge or champion promotion.
