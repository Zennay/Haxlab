# Replay imitation selection: exact input contract

The selected replay-player map is part of the derived imitation shard's provenance. The extractor now validates its JSON **before** reading the replay or creating an output shard.

Accepted input is a non-empty JSON object mapping canonical decimal, non-negative JavaScript-safe player IDs to non-empty string identities. Only the literal `0` can begin with zero; `01`, `+1`, `1.0`, `1e0`, negative IDs, whitespace and numbers outside the safe integer range are rejected. Object values must already be strings with at least one non-whitespace character.

Valid values are preserved byte-for-byte as JavaScript strings (including their surrounding spaces when present): they are not trimmed or coerced into strings. Validation rejects arrays, null, empty maps, malformed JSON and numeric/boolean/null identity values.

Repeated object keys are rejected **before** `JSON.parse`'s last-value-wins behavior can alter the selection. This includes identical raw keys (`{\"1\":\"Alice\",\"1\":\"Bob\"}`) and semantically identical keys written with JSON escapes (`{\"\\u0031\":\"Alice\",\"1\":\"Bob\"}`). The extractor reports `INVALID_SELECTED_PLAYER_MAP` / `invalid_selected_player_map:duplicate_player_id` and does not open a replay or publish a shard. Distinct canonical keys and all accepted identity strings retain their previous interpretation. This change affects input integrity only; sample cadence and the underlying replay remain unchanged. The extractor's row sampling, timing and replay semantics are unchanged.

The reusable `tools/extract_imitation_args.js` parser is dependency-free; `tools/extract_imitation.js` uses the same parser before creating output. This source-only change does not authorize replay ingest, learning runs, training/model promotion, a VPS job or production release. Required existing exact-head CI and reviewer/owner gates remain in place.

Ported from older PR-less branch `feature/data-pipeline-imitation-player-map-integrity-20261007` onto fresh current-main branch without copying its QA/test artifacts, preserving issue #452 scope.

## Sampler-interval: exacte datasetparameters

Dezelfde extractor weigerde vóór deze aanvulling geen half-geparseerde
optionele `sampleEveryTicks`-argumenten. `parseInt("6tekst", 10)` en
`parseInt("6.5", 10)` geven allebei `6`, terwijl de gebruiker de
werkelijke sampling-cadans niet eenduidig heeft opgegeven. Dit tast
reproduceerbaarheid en shard-herkomst aan.

Het nieuwe, herbruikbare `parseSampleEveryTicks` accepteert
bij afwezigheid van het optionele argument nog steeds de bestaande
standaard **6**. Bij expliciete invoer accepteert het alleen
een canonieke, strikt positieve, veilige gehele decimale waarde
(`1`, `6`, `250`); geen nul, negatief getal, voorloopnullen,
decimale breuk, exponent, achtervoegsel, spaties, lege waarde of
integer boven `Number.MAX_SAFE_INTEGER`. Ongeldige invoer veroorzaakt
`INVALID_SAMPLE_EVERY_TICKS` / `invalid_sample_every_ticks`
**vóór** de replay wordt gelezen of tijdelijke of definitieve
shardbestanden worden aangemaakt. Geldige waarden blijven exact
het bestaande samplinggedrag aansturen.

Dit is geen replayconversie, datamigratie of autorisatie voor training,
modelpromotie of het draaien van echte replaydata. De bestaande
exact-head CI, onafhankelijke review, bron-eigenaarschap en
deploymentvoorwaarden blijven intact.


## Geen lege trainingsshards als geslaagde extractie

De extractor kan een syntactisch geldige selectie ontvangen waarvan geen
enkele speler in gesamplede states voorkomt, of alle geselecteerde states
kunnen een onbekende/ongeldige inputlabel hebben. Voorheen verscheen dan
toch een succesvol gecomprimeerd outputbestand met **nul trainingsrijen**.
Downstream training of datasetcatalogi konden dat verwarren met een
bruikbare shard.

Bij `sampleCount === 0` weigert de bron nu finalisatie van de shard:
`no_selected_players_in_sampled_states` (geen speler waargenomen) of
`no_selected_samples_with_known_input` (wel staten, geen bruikbaar
inputlabel). De bestaande foutafhandeling vernietigt de schrijfstroom en
verwijdert de tijdelijke output. Alleen bij minstens één bruikbare
gesamplede trainingsrij mag de tijdelijke shard worden gepubliceerd als
definitief bestand. Een fout verleent geen toestemming voor herhaalde
replayverwerking, training of modelpromotie; de operator controleert
de geselecteerde IDs en de replay-/inputherkomst.

Dit is een fail-closed bronfix binnen bestaande PR #566, niet een claim
dat echte replays opnieuw zijn verwerkt. Bestaande verplichte CI, review
en eigenaarschapsgrenzen blijven van kracht. Er is geen QA-suite
toegevoegd of handmatig uitgevoerd.
