# Runtime event write contract

`RuntimeState.event()` is the generic producer boundary for HaxLab runtime
ledger events. It validates primitive text shapes before SQLite can coerce
values into the `runtime_events` table.

## Event type

`event_type` must be a native Python `str`, must not be empty, and must not
contain leading or trailing whitespace.

The write boundary deliberately does **not** freeze a list of accepted event
names. New event types can be added by producers without changing this
primitive contract.

## Subject and detail

`subject` and `detail` must be native Python strings. Empty strings are
valid because not every generic event requires both fields.

The content is otherwise preserved verbatim. In particular, `detail` may
contain multiline decoder or process-error evidence.

## Failure semantics

Malformed primitive values raise `ValueError` before the INSERT is executed.
No partial runtime-event row is committed for a rejected write.

## Deliberate non-scope

This contract does not change event retention, semantic runtime-ledger audits,
event taxonomy, replay processing, ingestion, analysis, M0 publication,
evaluation, or champion state.
