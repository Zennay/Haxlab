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

Snapshot capture is fail-closed. Only stable `active`, `inactive` and
`failed` states are accepted: `active` is captured, while `inactive` and
`failed` are treated as not active. Unknown units, transient states such as
`activating`/`deactivating`/`reloading`, empty state output, or an
otherwise indeterminate service-manager query abort the update before the first
service stop. The global snapshot is committed only after the complete scan, so
a late query failure cannot leave a partially populated recovery snapshot.

The snapshot is in stop order. Failure recovery restores the exact managed
service activation set rather than only restoring availability. It first stops
every managed unit that was inactive in the snapshot, using the normal stop
order. It then starts only the captured active units in reverse stop order, so
ingest is restored before worker and analyzer and the live bot is restored
last. This also covers late failures after the normal update path has already
started services that were inactive before the update.

## Managed-service stop gate

The update path is not the bootstrap path: `deploy/install-vps.sh` installs the
managed unit definitions before enabling them. During an update, stopping all
managed units is therefore a required precondition for mutating packages,
checkout state, Python dependencies or Node dependencies. A failed
`systemctl stop` aborts the pre-parsed update function immediately while the
recovery EXIT trap is armed; the updater does not continue into `apt-get` or
`git reset` after a failed stop.

## Stable updater execution

The updater resets `APP_DIR` to `origin/main`, which replaces the checked-out
`deploy/update-vps.sh` file itself. The destructive/update command sequence is
therefore defined as `haxlab_apply_verified_main_update` before the first
service stop. Bash parses the complete function body before invocation, so the
running update sequence is already fixed in the shell before `git reset --hard`
can replace the checkout. The function call is the final top-level updater
command; no deployment logic is read from the replaced script afterward.

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
