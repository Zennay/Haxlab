# Discord report attachment integrity

The match-report parser accepts attachment metadata only in its native JSON types.

## Contract

For recognized attachment aliases:

- file names must be non-blank strings;
- URLs, when present, must be non-blank strings;
- sizes, when present, must be native non-negative integers;
- a size of `0` is valid and must not be lost through truthiness fallback;
- numeric strings, booleans, objects and negative sizes are not coerced.

Malformed attachment entries are ignored individually, so a corrupt attachment cannot poison valid sibling attachments in the same report.

This boundary is deliberately limited to attachment parsing. It does not modify Discord export traversal, replay/report matching, M0 identity, quality/skill logic, runtime processing, or evaluation/promotion state.
