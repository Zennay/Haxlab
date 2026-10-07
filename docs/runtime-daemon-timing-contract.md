# Runtime ingest-daemon timing contract

The always-on ingest daemon owns two timing controls that directly affect replay
discovery: the polling interval and the minimum age before an incoming replay is
eligible for ingestion. Both are configuration boundaries and must fail closed.

## Poll interval

`--interval` is validated by argparse before directories are created or
`RuntimeState` is opened.

Accepted values:

- use canonical CLI text without leading or trailing whitespace;
- parse to a finite number;
- are greater than or equal to 1 second.

Rejected values include zero, sub-second values, negatives, NaN, either
infinity, overflow-to-infinity values, non-numeric text, and surrounding
whitespace variants.

After parsing, the daemon sleeps for the validated value directly. There is no
runtime `max(1.0, interval)` clamp, so invalid configuration cannot be silently
converted into live polling behavior.

## Minimum file age

`--minimum-file-age` uses the same canonical finite-number boundary but allows
zero. Zero intentionally means an otherwise eligible file may be considered
immediately. Negative, non-finite, malformed, overflowed, or whitespace-padded
values are rejected during argument parsing.

The production defaults remain unchanged:

- interval: 60 seconds;
- minimum file age: 30 seconds.

## Non-scope

This contract does not change replay discovery rules, scanner path handling,
archive publication, runtime state schema, worker/analyzer behavior, M0
artifacts, evaluation, or champion state.
