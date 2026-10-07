# Runtime status consumer integrity

`haxlab-status` is a read-side data-pipeline consumer. It must reject malformed runtime snapshots before calculating progress or ETA fields.

The consumer requires native non-negative integer replay/processing/analysis counts, native finite non-negative duration/rate metrics, processing totals that do not exceed raw replay inventory, and analysis totals that do not exceed successfully processed replays.

This contract prevents coercible, non-finite, negative, or internally inconsistent state from being rendered as plausible operational status.
