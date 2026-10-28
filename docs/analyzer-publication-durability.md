# Analyzer: crash-durable derived replay publication

The replay analyzer publishes derived JSON and subsequently records an `ok` result in the runtime SQLite ledger. `os.replace` alone makes the new file visible but does not guarantee that its containing directory entry has survived a sudden host/power failure.

The actual analyzer now performs this ordered publication boundary:

1. Open the output directory using directory/no-follow capabilities; on unsupported systems fail closed.
2. Produce the derived JSON in a temporary file **inside** the output directory; flush and fsync its bytes.
3. Atomically replace the target derived JSON pathname.
4. **Fsync the containing directory descriptor before** returning a successful analysis result that can be recorded as `ok` by `analyze_batch`.

If any publication/open/fsync operation fails, the analyzer returns a `derived_output_publish_error` failure instead of claiming success. Temporary names are cleaned where possible. A rename followed by a directory-sync failure can leave an uncommitted orphan file; the existing analyzer deliberately reruns orphan/uncommitted outputs rather than trusting them.

This is a current-main source-only integration of historical draft [#276](https://github.com/Zennay/Haxlab/pull/276), with the old branch and QA workflow untouched. No live replays, models, runner processes, ledger or archive are modified by this GitHub change. Existing mandatory exact-head CI, independent review and release approval apply.
