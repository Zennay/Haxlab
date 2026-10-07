# Runtime SQLite ledger audit

HaxLab's runtime SQLite database is coordination and provenance evidence for replay
ingest, replay processing, and versioned analysis. SQLite foreign keys protect
row existence, but they do not enforce the semantic contracts HaxLab relies on
for data-pipeline decisions.

The isolated `haxlab.runtime.state_audit` module performs a **read-only,
fail-closed** audit. It opens the database with SQLite `mode=ro`, enables
`query_only`, and pins all checks to one explicit read transaction so live
ingest/analyzer writes cannot make cross-record checks observe different
moments. It refuses a symlinked database path, runs SQLite `quick_check` and
`foreign_key_check`, then validates the runtime ledger contracts without
rewriting any state.

The v1 audit checks:

- required runtime tables are present;
- source, raw replay, processing, and analysis SHA-256 identities are canonical;
- sizes, mtimes, frame/event/sample counts, durations, and decompressed byte
  counts are non-negative and correctly typed;
- successful rows contain the metadata needed to prove success and do not retain
  stale error text;
- failed source rows cannot claim an archived replay SHA;
- failed and retry analysis rows retain the original non-empty failure evidence;
- statuses use the runtime's declared state vocabulary;
- archived/duplicate source records resolve to a raw replay with the same byte size;
- every versioned analysis row is backed by a successfully processed replay;
- successful analysis rows cannot reuse the same output path.

Run it directly without adding a console-script dependency:

```bash
python -m haxlab.runtime.state_audit /var/lib/haxlab/state/haxlab.sqlite3
```

The command emits deterministic JSON using schema
`haxlab-runtime-state-audit-v1` and exits `0` only when the ledger is clean;
any integrity finding exits `2`.

This lane intentionally does not inspect raw replay bytes or derived artifact
bytes. Those are owned by the existing archive/artifact audits. It also does not
modify `runtime/state.py`, ingestion, analyzer, selector, shard, evaluation, or
champion state.

Exact-head proof is staged in
`.github/workflows/data-pipeline-runtime-ledger-audit-proof.yml`. Ordinary
branch pushes cannot execute its self-hosted job; proof requires either manual
dispatch or an explicit commit whose message contains
`[runtime-ledger-proof]`. Integration into VPS control should happen only
after that focused proof is green and after the current serialized HaxLab runner
owner releases the self-hosted runner.
