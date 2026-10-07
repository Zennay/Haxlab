# Analyzer orphan artifact recovery

The runtime ledger is the authority for whether a replay has a committed analysis result for the current analyzer version.

When `list_unanalyzed_replays()` selects a replay, an existing JSON file at the derived output path is not trusted as completed evidence. It may be an orphan left by an interrupted run, stale output, or tampered evidence.

The analyzer therefore always reruns the decoder for selected pending/retry work and only then:

1. validates reconstruction completeness;
2. atomically replaces the derived JSON file;
3. commits the fresh analysis status and metrics to the runtime ledger.

If decoder execution fails, the replay is recorded as failed and any pre-existing orphan file is not promoted into successful ledger evidence.

This boundary is intentionally separate from the analysis artifact auditor/finalizer. The auditor reconciles already committed artifacts; this contract prevents uncommitted filesystem residue from becoming authoritative in the first place.
