# Canonical raw source receipt paths — issue #407

The HaxLab source bundle producer inventories `.hbr2` (case-insensitive extension) and `.json` import evidence recursively under the selected immutable export root. On POSIX, a literal backslash is a valid filename/ancestor character, but the canonical downstream source receipt verifier **rejects all `\\` path characters** as ambiguous/noncanonical. Previously the producer could successfully hash and emit an impossible-to-verify receipt.

The real producer now **rejects an importer-relevant path that contains any literal backslash**, including when the backslash belongs to a nested parent directory, before hashing that file or constructing the emitted canonical bundle. The stable reason is `source_path_noncanonical_backslash`, without the raw filename. The rule applies only after recognizing an importer-relevant extension; unrelated non-source filenames are not promoted into the manifest. Ordinary canonical relative POSIX paths, case-insensitive HBR2 recognition and existing file identity / no-follow / provenance checks remain unchanged.

This source-only repair is deliberately disjoint from `src/haxlab/ingestion/source_receipt_verifier.py`, CLI, ingestion/publisher, runtime, training, evaluation and model/champion promotion. A project must not try to work around the verifier rule by escaping or normalizing away source path bytes; that would misstate evidence identity.

No QA files, suites or workflow configs were authored or manually invoked. Existing exact-head CI, independent reviewer acceptance, branch protection and authorized production handoff remain mandatory before merge or runtime assertions. See [#407](https://github.com/Zennay/Haxlab/issues/407).
