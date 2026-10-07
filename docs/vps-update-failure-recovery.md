# VPS update failure recovery

`deploy/update-vps.sh` intentionally stops HaxLab services while replacing the
application checkout and dependencies. A fail-fast update must not turn an
ordinary git, package-install, unit-install or health-check failure into an
indefinite data-pipeline outage.

## Snapshot boundary

Before the first service stop, the updater records which update-managed units
are active:

- `haxlab-live-bot.service`;
- `haxlab-autonomy.timer`;
- `haxlab-autonomy.service`;
- `haxlab-analyzer.service`;
- `haxlab-worker.service`;
- `haxlab-ingest.service`.

The snapshot is in stop order. Failure recovery restores the exact managed
service activation set rather than only restoring availability. It first stops
every managed unit that was inactive in the snapshot, using the normal stop
order. It then starts only the captured active units in reverse stop order, so
ingest is restored before worker and analyzer and the live bot is restored
last. This also covers late failures after the normal update path has already
started services that were inactive before the update.

## Exit semantics

An EXIT trap is installed after the active-state snapshot and before the first
stop. On a non-zero updater exit it performs best-effort restoration. Failed
stop or start operations are recorded as recovery failure but do not prevent
the remaining restore operations, and recovery failure never replaces the
original updater exit status. The primary update failure therefore remains
authoritative for automation and operator diagnosis.

On a successful update the existing startup and health checks remain
authoritative. The recovery trap is disabled only after `haxlab-status`
returns successfully.

## Testability

The recovery logic lives in a pure shell helper and accepts the ambient
`systemctl` command. Focused tests replace `systemctl` with a shell function,
so capture, exact active/inactive state restoration, stop/start ordering,
partial restoration and exit-code preservation are tested without root access
or mutations to the real VPS service manager.
