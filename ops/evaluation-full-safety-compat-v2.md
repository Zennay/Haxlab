# Hardened evaluation full safety compatibility proof v2

Proof-only carrier for issue #303.

Canonical Arena-v2 base:
- `ee5ab508e9d8504de12b7cbb5fdeca43a58ae1a4`

Exact hardened product inputs:
- #286: `689e384540f5ac8f7cba6c84b3b55d75f3f66839`;
- #288: `9b6eb665693048348fc725a39dcde6ed7486840d`;
- #290: `b628530178ff8b92dcde8905fd6cbe8848d318e8`;
- #296: `108608984cf271e4054f9522fe10fcd41e52e93f`;
- #302: `95dcab1fe8f14f2cf75d95bb98acf742473b6d6d`.

This v2 proof extends the terminal-green #300 carrier with the corrected scenario-source selector configuration candidate. It overlays only those five exact product/test candidates on untouched canonical Arena-v2, materializes the same complete 19 additive evaluation safety contracts, then runs:
1. all 19 additive safety contracts;
2. all five hardened product regression suites;
3. adjacent canonical evaluation regressions.

Every referenced owner branch is checked against its recorded exact SHA before materialization. A green result proves compatibility only; it does not merge, resync, promote a champion, recalibrate thresholds, or change canonical/owner state. Close this carrier without merge after evidence capture.
