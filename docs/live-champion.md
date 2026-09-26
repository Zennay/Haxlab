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

## Manual live-room pilot

`tools/elite_live_room.js` turns the activated champion into a real
node-haxball CreateRoom test host. It is intentionally fail-closed:

- it accepts only the explicit `live.json` pointer;
- the pointer must already be at validation stage `live`;
- challengers and canary-only versions cannot be loaded by the room;
- the room is private by default;
- four in-memory HaxLab players spawn on one team;
- a human joining the room is moved to the opposite team and made admin;
- runtime status is emitted every five seconds.

Dry-run the exact VPS champion without opening a room:

```bash
sudo -u haxlab env NODE_PATH=/opt/haxlab/node_modules \
  node /opt/haxlab/tools/elite_live_room.js --dry-run
```

For a real room, copy `deploy/live-room.env.example` to
`/etc/haxlab/live-room.env`, store the HaxBall headless token in the token
file referenced there, install `deploy/haxlab-live-room.service`, then start
the service manually. Do not enable it by default yet.

If `HAXLAB_LIVE_STADIUM` is unset, the launcher uses default `Big` only as
a connectivity/control smoke test and prints a warning. Fidelity testing should
use the validated custom BFF Big v4 stadium.
