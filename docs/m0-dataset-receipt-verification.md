# M0 dataset receipt verification

The dataset receipt verifier is the fail-closed consumer for
`haxlab-m0-dataset-receipt-v1` evidence. It verifies both the stored receipt
structure and the current five-file M0 publication before downstream work may
treat that publication as the same dataset.

This verifier is intentionally separate from the semantic M0 cross-artifact
auditor. Semantic validation answers whether the artifacts agree with each
other. Receipt verification answers whether their exact bytes still match a
previously recorded publication identity.

## Accepted receipt evidence

The verifier accepts either:

- the receipt object returned by `build_dataset_receipt()`; or
- the exact success JSON emitted by
  `python -m haxlab.ingestion.dataset_receipt`, which adds `"ok": true`.

No other top-level fields are accepted. When `ok` is present it must be the
native JSON boolean `true`.

Receipt files are opened without following their final path component and must
be regular files. Duplicate JSON object keys are rejected instead of relying
on last-key-wins parsing.

The payload must preserve:

- schema `haxlab-m0-dataset-receipt-v1`;
- exactly five artifact records in canonical M0 order;
- exact artifact names;
- native non-negative integer byte sizes;
- lowercase 64-character SHA-256 digests;
- the canonical `receipt_sha256` over the receipt core.

## Dataset verification

After the stored evidence passes structural validation, the verifier rebuilds
the receipt from the current dataset root using the same no-follow,
regular-file contract as the producer.

Verification fails closed when the dataset is missing or unsafe, when any
artifact's bytes or size changed, or when the stored receipt no longer hashes
to itself. Byte drift is reported by canonical artifact name.

## Usage

```bash
python -m haxlab.ingestion.dataset_receipt /path/to/m0-output > m0-receipt.json
python -m haxlab.ingestion.dataset_receipt_verify \
  /path/to/m0-output \
  m0-receipt.json
```

A successful verification exits 0 and emits
`haxlab-m0-dataset-receipt-verification-v1` JSON containing the verified root
receipt SHA-256 and artifact count. Failure exits 2 with machine-readable JSON
on stderr.

## Integration rule

Do not use receipt verification as a substitute for semantic validation.
A downstream publication gate should require both when it needs to prove that
an M0 dataset is internally coherent and still byte-identical to the recorded
evidence.
