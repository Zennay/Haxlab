# Analyzer derived-output publication durability

HaxLab must not record a replay analysis as successful until the corresponding derived JSON directory entry is durable enough to survive a host crash.

## Required invariant

After decoder output has been serialized, the analyzer must:

1. fsync the temporary JSON file;
2. atomically replace the final derived JSON path;
3. fsync the containing derived-output directory;
4. only then write replay_analysis status `ok` to SQLite.

If secure directory opening or directory fsync fails, the analyzer records the attempt as `failed` and must not attach an `output_path` to a success row. A JSON file that was renamed before a directory-sync failure is treated as orphan evidence; existing orphan-recovery behavior recomputes it on retry instead of trusting it.

## Boundary

This lane is intentionally distinct from:

- raw archive publication durability (#246), which protects the immutable replay archive;
- runtime SQLite backups and ledger audit (#102/#104);
- orphan-output recomputation (#207/#210);
- analyzer raw-archive identity verification (#231/#234);
- decoder semantics, evaluation, model, threshold, and champion state.

## Proof

`tests/test_analyzer_publication_durability.py` proves that directory fsync occurs before an `ok` ledger write, that directory-fsync failures become failed analysis attempts, and that platforms without the required no-follow directory flags fail closed.

The branch-scoped self-hosted workflow compiles the repository, runs the focused contract, and reruns adjacent analyzer archive-integrity and orphan-recovery regressions.
