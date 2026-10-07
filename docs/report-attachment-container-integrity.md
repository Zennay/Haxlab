# Discord report attachment-container integrity

Discord export messages are untrusted JSON. The report parser only iterates `attachments` when the container is a native JSON list.

## Contract

- missing or `null` attachments behave as an empty list;
- scalar, object, boolean and string attachment containers also behave as empty rather than raising during report detection or parsing;
- malformed entries inside a valid list remain isolated individually;
- a malformed earlier message cannot abort discovery of later valid reports in the same export;
- valid list-backed attachment parsing is unchanged.

The fix is intentionally inside `ingestion/reports.py`; `discord_export.py`, ingestion publication, replay matching, runtime, learning and evaluation state are unchanged.
