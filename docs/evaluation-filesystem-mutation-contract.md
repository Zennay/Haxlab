# Evaluation filesystem-mutation contract

HaxLab evaluation gates run inside exact-head validation and must not be able to
silently destroy, relocate, relink, truncate, or mutate metadata of existing
filesystem objects.

## Required invariant

Production Python modules recursively below `src/haxlab/evaluation/` must not
invoke destructive filesystem identity/content-metadata operations, including:

- remove/unlink/rmdir and recursive removal;
- rename/replace/move;
- hard-link or symlink creation;
- truncate/ftruncate;
- chmod/chown and related metadata mutation;
- utime and extended-attribute mutation.

The contract resolves direct module calls, imported aliases, assignment aliases
and constant-`getattr(...)` spellings. For `pathlib.Path`, it tracks annotated
Path parameters and locally constructed/resolved Path values.

## Preserved behavior

Canonical evaluation CLIs are allowed to create their explicit output
directories and write their explicit result files through the existing
`Path.mkdir(...)` + `Path.write_text(...)` pattern. Read-only filesystem
inspection is also allowed. Ordinary non-filesystem methods such as
`str.replace(...)` are not classified as path mutation.

## Boundary

This lane is intentionally separate from:

- #160/#162, which owns import-time side effects;
- #112, which owns strict evidence parsing/loading;
- #200/#201, which owns process-global state mutation;
- #151/#153, which owns network/subprocess hermeticity;
- #218/#220, which owns in-memory immutable evidence-object bypasses;
- #221/#222, which owns foreground liveness/non-interactivity;
- #92, which remains the owner-reserved canonical CI blocker.

It changes no evaluation implementation, thresholds, models, champion pointers,
source suites, calibration inputs, evidence formats, or canonical Arena-v2
workflow.

## Proof

`tests/test_evaluation_filesystem_mutation_contract.py` recursively scans the
evaluation package with Python ASTs. Its self-tests cover direct `os` and
`shutil` calls, imported aliases, assignment aliases, constant-`getattr`,
annotated/constructed `Path` values, and callable aliases while freezing the
allowed output-creation and read-only cases.

The branch-scoped self-hosted proof verifies the exact SHA is still the live
branch head, compiles the evaluation package and contract, runs the focused
contract, then runs the adjacent canonical Arena-v2 evaluation regressions that
are not blocked by owner-reserved #92.

A green result is validation evidence only. It does not authorize PR #19 merge,
canonical resync, or champion promotion.
