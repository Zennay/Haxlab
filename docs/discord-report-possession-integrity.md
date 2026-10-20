# Discord match report: two-team possession integrity

HaxLab's raw Discord match report can contain two possession percentages, red and blue. These percentages describe one complete two-team partition and must **not** be admitted independently if their sum is inconsistent.

The actual `parse_match_report()` consumer now reads the pair together. When both percentages are present, it requires each to lie within [0,100] and their sum to be within **0.5 percentage point** of 100, inclusive, to allow customary rounding (e.g. 50.2/49.7). Source values like 60/30 or 150/20 are rejected with `ValueError("invalid_possession_percentages")` instead of being published as plausible match evidence.

Reports without parsed possession evidence keep their existing `None` fields. Scoring, attachments, report ID, replay pairing and source archive semantics remain unchanged.

The change ports historical source-owner draft [HaxLab PR #413](https://github.com/Zennay/Haxlab/pull/413) to an independent fresh-main source branch without touching that draft or reusing its QA files. No new tests or proof workflow authored or run. Existing mandatory CI, reviewer, protected-main and release admission must be satisfied before live integration.
