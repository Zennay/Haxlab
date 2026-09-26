# HaxLab live champion

The live runtime uses an explicitly activated champion rather than the newest
candidate automatically.

Current validated candidate:

- version: `frozen-future-v01-386d0218a928`
- formation: GK / DM / AM / ST
- learned future assist: DM + AM only
- minimum future confidence: 0.68
- minimum ball distance: 80
- scripted recovery: disabled
- multi-replay gate: passed
- long-horizon canary gate: passed
- actual plugin runtime gate: passed

Activation writes `/var/lib/haxlab/derived/champions/elite-player/live.json`
atomically. The bot reads that pointer at game boundaries only. Candidate
promotion and live activation remain separate so a newly trained model cannot
replace the live policy before all validation stages pass.

A rollback activates a previous runtime-validated immutable champion version;
the activation history preserves the previous live version.
