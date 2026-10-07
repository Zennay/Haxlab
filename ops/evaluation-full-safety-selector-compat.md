# Hardened evaluation + scenario selector full safety compatibility proof

Proof-only carrier for issue #306.

Canonical Arena-v2 base:
- `ee5ab508e9d8504de12b7cbb5fdeca43a58ae1a4`

Exact hardened product inputs:
- #286: `689e384540f5ac8f7cba6c84b3b55d75f3f66839`;
- #288: `9b6eb665693048348fc725a39dcde6ed7486840d`;
- #290: `b628530178ff8b92dcde8905fd6cbe8848d318e8`;
- #296: `108608984cf271e4054f9522fe10fcd41e52e93f`;
- #302: `95dcab1fe8f14f2cf75d95bb98acf742473b6d6d`.

The proof overlays only those five exact product/test candidates on untouched canonical Arena-v2, materializes the complete 19 additive evaluation safety contracts from the previously green v6 proof, then runs:
1. all 19 additive safety contracts;
2. the five hardened product regression suites;
3. adjacent canonical evaluation regressions.

Every referenced owner branch is checked against its recorded exact SHA before materialization. A green result proves compatibility only; it does not merge, resync, promote a champion, recalibrate thresholds, or change canonical/owner state. Close this carrier without merge after evidence capture.

The #302 owner remains authoritative for scenario-source implementation; this carrier only overlays its exact head inside CI and never edits that branch.
