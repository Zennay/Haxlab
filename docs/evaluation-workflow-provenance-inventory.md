# Evaluation workflow provenance inventory

This lane adds a read-only inventory for the canonical HaxLab evaluation-validation
workflow set. It exists to help parallel workers identify provenance debt without
editing workflows that already have an active owner.

## Why this is separate from workflow owners

The Arena-v2 canonical branch currently has many narrow workflow-hardening lanes.
The inventory deliberately changes none of those workflow files. It reads an
explicit list of evaluation/Arena workflows and reports observable provenance
properties for the exact repository bytes that were checked out.

That makes the output coordination evidence, not promotion authority. A finding
must still be handled by the workflow's current owner or by a newly reserved
owner-safe lane after a fresh issue/PR/branch ownership check.

## Reported properties

For every explicitly listed workflow the report includes:

- every `uses:` action reference and which external refs are not immutable
  40-character commit pins;
- whether checkout disables persisted credentials;
- whether checkout requests a clean working tree;
- whether every checkout ref is provenance-bound, either to the exact event/source expression or to a literal 40-character immutable commit (for frozen secondary sources);
- whether the workflow merely records HEAD and whether it contains a real source-bound exact-HEAD comparison guard;
- whether top-level `contents: read` is present;
- any top-level or job-level permission entries that grant `write` (parsed from real `permissions:` blocks, not shell text);
- runner specifications and whether every job is exactly `[self-hosted, haxlab]`;
- job timeouts and whether every runner job has a bounded 1–480 minute timeout;
- any explicit `continue-on-error: true` failure masking;
- a deterministic list of findings.

The tool does not parse or execute workflow expressions, dispatch Actions, inspect
`/var/lib/haxlab`, contact a model service, or mutate champion/promotion state.

## Explicit workflow set

The default inventory is intentionally explicit rather than discovered with
filesystem globbing. It covers the 16 mandatory validation workflows plus the
canonical calibration, multisource freeze, HaxLab CI, and calibration-summary
validation workflows.

This explicit set is reviewable and prevents a surprising file elsewhere in the
repository from silently entering evaluation acceptance evidence.

A `workflow_dispatch` checkout using `inputs.ref` is treated as immutable only when that exact input variable is validated against lowercase 40-hex SHA syntax and invalid values fail non-zero. Merely accepting an input named `ref`, or mentioning a SHA regex elsewhere, remains a finding.

## Usage

Report-only mode always returns zero unless the input itself is invalid:

```bash
python3 tools/evaluation_workflow_provenance_inventory.py
```

Audit only selected workflow paths:

```bash
python3 tools/evaluation_workflow_provenance_inventory.py \
  --workflow .github/workflows/arena-v2-evaluation-validation.yml \
  --workflow .github/workflows/multisource-suite-v2.yml
```

For a caller that wants any finding to be a hard gate, add `--strict`. Strict
mode returns exit code 2 when findings exist; it does not modify the repository
or any external state.

## Safety and interpretation

A mutable action ref, write permission, or missing provenance property is a
finding to review, not an automatic instruction to edit that workflow. Before
opening a fix lane, recheck open issues, PRs and branches for exact-path
ownership.

Recording `git rev-parse HEAD` by itself is deliberately not counted as a guard; the inventory requires a source-bound comparison so log-only provenance cannot look equivalent to fail-closed validation. Equality assertions count only when the same step enables shell errexit (for example `set -euo pipefail`) or the assertion explicitly exits non-zero on failure. A `!=` comparison is accepted only when that mismatch branch exits non-zero; inverted, non-enforcing, or log-only checks remain findings.

Likewise, a clean inventory is only source-level provenance evidence. It does not
prove calibration quality, multisource correctness, model strength, champion
eligibility, or Arena-v2 merge readiness.
