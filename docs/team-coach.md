# HaxLab Team Coach

HaxLab targets fixed 4v4 play with the role spine **GK - DM - AM - ST**.

The coach is deliberately evidence-first. It turns replay measurements into
coaching hypotheses and always includes the metric/event evidence behind a
finding instead of generating unsupported tactical prose.

## Usage

Analyze a raw replay directly on a HaxLab machine:

```bash
haxlab-coach match.hbr2 --team both
```

Analyze only the team containing a known player:

```bash
haxlab-coach match.hbr2 --team-player "Ado"
```

The input can also be an existing `state-pass-v4` JSON:

```bash
haxlab-coach /var/lib/haxlab/derived/state-pass-v4/ab/cd/<sha>.json --team red
```

Machine-readable output:

```bash
haxlab-coach match.hbr2 --team-player "Ado" --format json --output coach.json
```

## Current evidence

The v1 coach uses:

- explicit 4v4 GK/DM/AM/ST role inference on a common attack axis;
- role confidence and substitution fallbacks;
- touch-chain retention and turnovers;
- pressure retention;
- progression;
- recoveries;
- own-half turnover risk;
- quick recovery/counterpress after a turnover;
- direct GK-DM, DM-AM and AM-ST touch-transfer links;
- average longitudinal team shape and line gaps;
- role-specific feedback and drills.

The text report contains team shape, build-up/connectivity, transitions/risk,
priorities, player-by-player coaching and a short training plan.

## Limits

Average position is not the same as frame-perfect tactical positioning. The
current coach therefore labels spacing findings as evidence from average shape.
A later coach pass can add temporal shape windows (possession, defensive
transition, settled defense, settled attack) without changing this report
schema.
