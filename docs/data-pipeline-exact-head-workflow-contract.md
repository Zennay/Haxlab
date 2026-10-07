# Data-pipeline exact-head workflow contract

GitHub Actions gives `github.sha` different semantics depending on the event. For a
`pull_request` workflow it identifies GitHub's synthetic merge ref, not necessarily
the pull-request branch head.

That distinction matters when a HaxLab validation workflow is described as
**exact-head evidence**. A green merge-ref run can be useful integration-preview
evidence, but it must not be recorded as proof of a different branch-head SHA.

## Contract

For `data-pipeline-*.yml/.yaml` workflows that:

- claim `exact-head` or `exact head` semantics;
- run on `pull_request`; and
- use `actions/checkout`;

the workflow must explicitly bind the checkout/verification path to
`github.event.pull_request.head.sha`.

Dispatch-only and push-only workflows are not affected by this PR merge-ref rule:
their event SHA can legitimately be the exact revision being validated.

## Migration behavior

Current main has one grandfathered PR-triggered offender:
`data-pipeline-autonomy-status-proof.yml`. That workflow is already corrected on
PR #417.

The regression uses a **shrinking grandfather set**. Removing/fixing an existing
offender remains green without requiring a synchronized allowlist edit, but adding a
new offender fails immediately. This lets active owner lanes retire the legacy case
without this contract taking ownership of their workflow files.
