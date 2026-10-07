# Runtime event retention contract

HaxLab's VPS runtime records ingest lifecycle events in SQLite table `runtime_events`.
The status surface only needs recent evidence, while a permanently running daemon can
otherwise grow this append-only table without bound.

`python -m haxlab.runtime.event_retention STATE_DB` provides a deliberately narrow
maintenance boundary for that table.

## Safety contract

- The database path must already exist as a regular file.
- Symlink database paths and paths traversing a symlink fail closed before SQLite opens.
- The exact five-column `runtime_events` schema is verified inside the same write
  transaction before any deletion.
- Retention uses one `BEGIN IMMEDIATE` transaction, so eligibility and deletion are
  evaluated against one SQLite snapshot.
- Rows newer than the age horizon are always retained.
- Independently, the newest `--keep-latest N` rows are retained even when older than
  the horizon. Ordering is `created_at DESC, id DESC`.
- A row exactly on the cutoff is retained; only `created_at < cutoff` is eligible.
- `--max-delete N` bounds each write transaction; the default is 5,000 rows. The oldest eligible rows are selected first with `id` as the deterministic tie-breaker.
- Selection and deletion use bounded SQL subqueries rather than one bind parameter per row, so batches larger than SQLite's traditional variable limit remain valid.
- `--dry-run` calculates the exact same bounded deletion set, then rolls the transaction back.
- Repeated bounded runs converge deterministically; once all eligible rows are gone, another run at the same evaluation time is idempotent.
- This module touches only `runtime_events`; it does not mutate source, raw replay,
  processing, analysis, M0, training, evaluation, or champion state.

Defaults retain seven days and at least 10,000 newest events:

```bash
python -m haxlab.runtime.event_retention /var/lib/haxlab/state/runtime.sqlite3
```

Preview first:

```bash
python -m haxlab.runtime.event_retention \
  /var/lib/haxlab/state/runtime.sqlite3 \
  --keep-hours 168 \
  --keep-latest 10000 \
  --max-delete 5000 \
  --dry-run
```

## Evidence

Success emits one canonical compact JSON object with schema
`haxlab-runtime-event-retention-v1`. It records the evaluated timestamp, cutoff,
rows before/eligible/selected/deleted/remaining/after, the configured batch cap, and
the first/last selected IDs. Dry-run receipts report `rows_deleted=0` while preserving
the same bounded selection evidence.

Failure emits `{"ok":false,...}` and exits 2. Unexpected schema or unsafe path
conditions are failures rather than best-effort cleanup.

This contract is intentionally separate from the read-only runtime-ledger integrity
audit tracked in issue #102.
