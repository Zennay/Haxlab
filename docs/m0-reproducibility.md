# M0 reproducibility and publication contract

HaxLab treats the Discord export and HBR2 files as immutable source evidence.
The M0 importer may be rerun from that evidence, so its derived dataset must be
reproducible independently of host paths, file creation order, and previous
contents of the derived directory.

## Canonical artifact set

One M0 dataset currently consists of exactly these canonical publications:

- `manifest.json`
- `replays.json`
- `duplicates.json`
- `reports.json`
- `matches.jsonl`

The five files form one logical dataset. A consumer must not infer that one file
is trustworthy merely because that individual file is valid JSON.

## Determinism requirements

For equivalent immutable source evidence and identical importer configuration:

1. Source file creation order must not change any canonical artifact bytes.
2. Discord message order must not change canonical output ordering.
3. Duplicate replay discovery must choose and publish the same canonical and
   duplicate paths regardless of physical creation order.
4. A rerun must restore the same canonical bytes after derived artifacts are
   modified or truncated.
5. Provenance stored in derived artifacts must be relative to the supplied raw
   export boundary. Absolute host, user, checkout, or VPS paths are not stable
   dataset evidence.
6. Canonical record identifiers must be unique and deterministic.
7. Publication must eventually become generation-atomic: readers should observe
   either the previous complete M0 generation or the next complete generation,
   never a mixture.

The regression surface for the currently proven successful-input rules lives in
`tests/test_m0_determinism_contract.py`.

## Cross-artifact validation

Draft PR #88 adds a separate read-only audit boundary. Its job is to detect
derived-state drift without mutating raw or derived data. In particular, the
audit binds manifest counts and unmatched sets to the replay, duplicate, report,
and match artifacts; verifies replay/report references; rejects unsafe source
paths and symlinked required artifacts; and requires canonical match identity.

Producer correctness and consumer validation are intentionally separate. A
validator detecting a bad dataset is not a substitute for preventing the
producer from publishing that dataset.

## Known producer gaps

The current audit work identified three producer-side follow-ups without
modifying files owned by active workers:

- GitHub issue #89: Discord JSON/message failures still publish absolute
  `str(path)` provenance. Equivalent malformed exports mounted at different
  roots can therefore produce different manifests.
- GitHub issue #90: distinct reports can share one parsed `report_id`, allowing
  duplicate canonical `match_id` values unless publication fails closed or
  applies a documented deterministic disambiguation policy.
- GitHub issue #91: per-file atomic replacement does not make the entire
  five-artifact dataset generation atomic. A crash between replacements can
  expose a mixed generation.

These are producer fixes. They should remain isolated from the independent
validation and determinism lanes until their active owning scopes are free.

## Validation order

Before integrating a change that affects M0 publication:

1. run the focused M0 importer tests;
2. run `tests/test_m0_determinism_contract.py`;
3. when PR #88 is integrated, run its cross-artifact audit regressions;
4. run the repository HaxLab CI on the exact integration head;
5. only treat evidence from that exact head as merge evidence.

A newer commit supersedes older CI evidence, even when the older run was green.
