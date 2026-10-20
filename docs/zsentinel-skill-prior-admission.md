# Skill V0 — strict prior admission

This product guard closes the original skill-estimator configuration gap in [#374](https://github.com/Zennay/Haxlab/issues/374). It integrates the **producer behavior** from existing held [PR #385](https://github.com/Zennay/Haxlab/pull/385) against canonical current main, without adopting its legacy QA workflow or editing the original owner branch.

The existing `estimate_player_skill_v0()` rejects every malformed caller prior **before any observation arithmetic**:

- `prior_mean` must be an exact native `int` or `float`, finite.
- `prior_weight` must be an exact native `int` or `float`, finite and strictly greater than zero.

A boolean, numeric string, subclass, NaN/Infinity, zero/negative weight or arbitrary object cannot masquerade as a valid prior. Invalid inputs raise stable `ValueError` diagnostics `invalid_skill_estimator:prior_mean` / `invalid_skill_estimator:prior_weight`. Default `prior_mean=0.0`, default `prior_weight=5.0`, leaderboard `prior_weight=8.0`, observation weighting and skill dimensions retain their previous behavior.

**Scope:** only the estimator and this contract; no changes to skill observations, replay ingestion, model/champion promotion, runtime services, artifacts or live state. No new QA, regression, or workflow files and no manual suites. Existing required exact-head CI, independent review, repo protection and authorized release must pass before merge/deploy. Source code committed on a product branch does not prove live HaxLab recovery.

## 20 oktober · Extreme invoer mag de ranking niet vergiftigen

Ook een numeriek geldige Python `int` kan te groot zijn om naar een
eindige `float` om te zetten. De schatter vertaalt dat gecontroleerd naar
het reeds bestaande, veldspecifieke `invalid_skill_estimator:prior_*`
foutcontract. Bovendien kunnen twee afzonderlijk eindige priorwaarden
bij hun product toch naar oneindig overlopen. Dat geeft nu
`invalid_skill_estimator:prior_product` vóór de eerste observatie.

Dezelfde bron weigert nu de zeldzamere maar echte overflows bij
**geobserveerde gewichten, contextgecorrigeerde prestaties,
gewogen bijdragen en posterior totalen** met veldspecifieke
`invalid_skill_estimator:*` redenen. Dat verhindert een deels berekende
of niet-eindige spelersranking; de normale bestaande gewichten en
de standaard- en leaderboardpriors blijven ongewijzigd.

Dit is een uitbreiding op de **bestaande** [product-PR #580](https://github.com/Zennay/Haxlab/pull/580);
geen QA-werk of nieuwe PR. Uitrol van modeluitkomsten, training,
kampioenselectie en runtime blijft uitdrukkelijk buiten deze wijziging.
Exact-head verplichte CI en onafhankelijke acceptatie bepalen of dit
veilig op main mag landen.

The overall player `effective_weight` is now accumulated with the same
finite-only contract even if every per-dimension metric is missing: extremely
large, otherwise valid exposure weights cannot produce a non-finite top-level
result. The source rejects it before constructing `PlayerSkillEstimate` with
`invalid_skill_estimator:effective_weight_overflow`. All routine values,
missing-dimension behavior, and default weights are unchanged.
