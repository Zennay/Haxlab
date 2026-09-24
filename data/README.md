# Local data layout

Raw and derived datasets are intentionally not committed to Git.

Recommended layout:

```text
data/
  raw/
    discord-export/
  processed/
    m0/
  models/
```

Keep the DiscordChatExporter JSON and its assets directory together under `data/raw/`.

Run:

```bash
python -m haxlab ingest data/raw/discord-export --output data/processed/m0
```

The importer never modifies the raw export.

M0 output:

- `manifest.json` — counts, failures and unmatched sources;
- `replays.json` — unique replay inventory and validation;
- `duplicates.json` — content duplicates;
- `reports.json` — parsed Discord reports;
- `matches.jsonl` — canonical replay/report matches with confidence and reasons.

If an HBR2 or JSON file is malformed, the error belongs in the manifest instead of being silently ignored.
