# HBR2 replay basic payload-presence contract

`validate_replay_basic()` is the low-cost HBR2 v3 preflight used by ingestion inventory and immutable raw archive admission. Previously a replay containing exactly a 12-byte supported header with a positive frame count could appear basic-valid despite containing **no replay payload**.

A valid basic replay must now provide a readable supported 12-byte header, a positive `total_frames` and at least one additional byte beyond the header. The absence of payload data is rejected as `truncated_payload`; `invalid_total_frames` takes precedence when a replay has both defects. Version, duration and total-frame metadata remain attached to these explicit rejections.

This is a cheap presence check, **not** full raw-DEFLATE integrity validation; deeper replay/worker parsing still owns compressed data validity. The existing implementation reads header and file size separately; it does not promise a single-descriptor atomic snapshot under concurrent external file replacement. Stable final-entry replay descriptors are covered separately by PR #569.

Integrated product-only source from historical draft #334 onto current main. No tests, proof workflows, replay archive, live worker, model or promotion state were added/changed. Exact-head CI, independent review and release authority still govern merge.
