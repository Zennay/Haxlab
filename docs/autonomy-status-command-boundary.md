# Autonomy status-command boundary

The autonomy tick runs with `set -euo pipefail`, but failure of the status producer is expected to become explicit control evidence rather than an unrecorded shell exit.

## Contract

- a non-zero `haxlab-status` command is handled inside a guarded conditional;
- the tick writes `autonomy-status.json` with state `FAILED_RETRYABLE` and action `status_command_failed`;
- downstream leaderboard, manifest, training and generation work does not start after that failure;
- the tick exits safely after recording the retryable state;
- successful status output continues through the existing strict `haxlab.runtime.autonomy_status` parser, including its separate `invalid_status_snapshot` path.

No runtime-state schema/query, model training, evaluation, champion selection or promotion behavior is changed.
