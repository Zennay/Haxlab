# Evaluation hermeticity contract

HaxLab evaluation and promotion gates must decide from explicit, locally supplied evidence. The Python decision package must not make authorization depend on mutable network state or on child-process/shell execution hidden inside a gate.

## Contract

Production Python modules under `src/haxlab/evaluation/` are recursively checked for:

- `subprocess` use;
- direct socket networking;
- common HTTP/network clients (`requests`, `httpx`, `aiohttp`, `urllib.request`, `http.client`);
- shell execution through `os.system()` or `os.popen()`;
- asyncio subprocess creation.

Local immutable/file evidence and the existing read-only/local SQLite scenario-source path remain allowed. Node Arena execution stays an explicit workflow/tool boundary outside the Python decision package. The separately owned `evaluation/resync_guard.py` is the single approved local subprocess exception because it runs a bounded local `git diff`; only `subprocess.run` is allowed there, while networking and shell APIs remain prohibited.

This contract is intentionally narrow: it prevents hidden external side effects without changing evaluation algorithms, thresholds, evidence schemas, models, champion state or orchestration. The explicit resync-guard exception prevents this contract from conflicting with active PR #84 ownership.

## Integration

This lane is stacked directly on canonical Arena-v2 and is separate from promotion, calibration, scenario-source, evidence-I/O, policy-config, schema, resync and cross-workflow owners. Exact-head self-hosted proof is required before integration.
