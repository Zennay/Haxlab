# Multisource suite v2 workflow provenance contract

The multisource-suite-v2 workflow turns repository code plus existing immutable HaxLab evidence into a frozen manifest and uploads that manifest as acceptance evidence. Both the source checkout and the artifact uploader are therefore part of the provenance boundary.

## Invariants

`.github/workflows/multisource-suite-v2.yml` must:

1. pin every official GitHub Action to an immutable 40-character commit SHA;
2. pin `actions/checkout` to reviewed v4 commit `11d5960a326750d5838078e36cf38b85af677262`;
3. pin `actions/upload-artifact` to GitHub-verified v4 commit `ea165f8d65b6e75b540449e92b4886f43607fa02`;
4. checkout exact `github.sha` with `clean: true` and `persist-credentials: false`;
5. fail closed unless `git rev-parse HEAD == github.sha` before regressions or any read of live multisource evidence;
6. preserve read-only permissions, `[self-hosted, haxlab]`, timeout, canonical/manual triggers, regression scope, freeze/self-consistency ordering, artifact name/path and retention.

The hardening changes no suite implementation, scenario source, calibration-resume logic, evaluation config, frozen bytes, generated manifest semantics, model, threshold, champion pointer or promotion state.

## Source-only proof

`.github/workflows/multisource-suite-v2-workflow-provenance-proof.yml` is manual-only. It proves the exact dispatched/live branch head and the workflow contract without reading `/var/lib/haxlab`, creating the suite manifest, or uploading an artifact.

Keep this proof undispatched while canonical `#100 → #436` sequencing remains owner-held. The hardened canonical workflow remains the authority for actual freeze + upload evidence after integration.
