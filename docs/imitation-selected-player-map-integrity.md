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


## Idempotente shardpublicatie zonder overschrijven

Een herhaalde extractie kon met `renameSync` een bestaand definitief
`.jsonl.gz`-bestand vervangen, óók wanneer die shard bij een ander
selectie- of samplerprofiel hoorde. Daardoor kon een eerdere dataset
stilzwijgend verdwijnen en de provenance van downstream training
onbetrouwbaar worden.

De extractie maakt nu een willekeurig benoemd, exclusief gecreëerd
privé-tijdelijk bestand (`wx`, mode 0600) aan. Na succesvolle,
niet-lege compressie wordt het via een atomische hardlink in
**dezelfde directory** uitsluitend onder een **nog niet bestaande**
definitieve bestandsnaam gepubliceerd. Bij `EEXIST` blijft de
bestaande shard intact en wordt de nieuwe tijdelijke output volgens
de bestaande foutafhandeling opgeruimd. Filesystemen zonder
hardlinkondersteuning weigeren de publicatie liever dan een
niet-atomisch of overschrijvend alternatief te gebruiken.
Ook vroege schrijf- of compressiefouten worden vóór publicatie zichtbaar.

Dit verandert niet het binaire sampleformat, de labels, cadence
of replaytraining. Om een andere dataset te bouwen, kiest de
operator een nieuwe outputnaam. Geen bestaande shard, manifest,
model of productie-queue is in deze bronwijziging aangepast.

## Brongebonden SHA-256 voor herleidbare trainingsshards

De succesvolle JSON-samenvatting van de extractor bevat nu drie extra
hexadecimale SHA-256-receipts:

- `sourceReplaySha256` — de volledige originele `.hbr2`-bytes, niet
  alleen de mogelijk gedeelde bestandsnaam.
- `selectedPlayerMapSha256` — de **letterlijke UTF-8 JSON-argumentbytes**,
  inclusief expliciete keyvolgorde en schrijfwijze; dit is geen genormaliseerde
  selectiehash.
- `shardCompressedSha256` — de werkelijk naar de outputstream gepipete
  **gecomprimeerde gzip-bytes**, vastgesteld na volledig sluiten van de
  output en vóór create-only publicatie.

Hierdoor kunnen operators expliciet vastleggen welke bron, aangevraagde
selectie en fysieke shard bij elkaar horen, ook bij gelijknamige replays of
verschillende selectie-invoer. De hash wordt met de bestaande Node
`crypto`-module opgebouwd; de gecomprimeerde shard wordt niet volledig
opnieuw in het geheugen geladen. De bestaande JSON-samenvatting behoudt
schema `haxlab-imitation-extract-summary-v2` en bestaande velden.

Deze receipts tonen **byte-identiteit**, niet de kwaliteit van labels,
compleetheid van een trainingsdataset, actuele owner/CI-status of
toestemming voor een modelrelease. Geen replaydata is voor deze wijziging
verwerkt, geen bestaande shards of modellen zijn gemigreerd.


## Optionele persistente provenance-receipt naast de shard

De bestaande extractor print bij succesvolle omzetting al een
machineleesbare samenvatting met drie SHA-256's. De meeste
offline batch-/workerprocessen bewaren stdout echter niet automatisch.
Daarom is nu **optioneel** een vijfde positioneel argument na
`sampleEveryTicks` beschikbaar: het doelpad voor een afzonderlijke,
privé provenance-JSON.

```sh
node tools/extract_imitation.js replay.hbr2   /private/shards/game-001.jsonl.gz '{"1":"selected-player"}' 6   /private/receipts/game-001.receipt.json
```

De ontvangende directories moeten vooraf bestaan en onder beheer van
de bevoegde operator staan. Bij opgegeven receipt-bestemming schrijft
de extractor **eerst** de complete JSON-samenvatting naar een
willekeurig exclusief aangemaakt tijdelijk bestand met mode 0600.
Na volledig gesloten, niet-lege compressie publiceert hij de shard
en vervolgens de receipt via afzonderlijke **create-only hardlinks**
in hun respectieve directory. Geen bestaand doelbestand wordt
overschreven; de receipt mag niet hetzelfde pad gebruiken als de
shard of originele replay.

Omdat twee verschillende bestandsnamen niet atomair in één
bestandssysteemtransactie gepubliceerd kunnen worden, is de receipt
een **tweede, expliciete leveringsstap**. Als publiceren van de
receipt mislukt, eindigt het proces met een fout en géén succesbericht
op stdout. De eerder al gepubliceerde shard kan dan bestaan zonder
complete provenance-levering; verwijder of herschrijf die niet
blindelings en geef de dataset geen vrijgave. Inspecteer haar
`shardCompressedSha256` en een nieuw toegestaan uitvoerpad.
Een gewone run zonder receipt-argument blijft exact één
`.jsonl.gz` plus de bestaande JSON-uitvoer op stdout leveren.

De receipt legt identiteitsbewijzen vast, geen oordeel over dataset- of
modelkwaliteit, een workerclaim, review of productiepromotie.
Geen replay, bestaande shard, trainingsjob of broker is door deze
bronimplementatie gestart of aangepast.


## Vroege doelcontrole en volledige foutopruiming

Een herhaalde extractie naar een al bestaande shard- of receiptnaam
hoeft niet langer de volledige replay te decoderen voordat de
`create-only`-publicatie op `EEXIST` faalt. Na validatie van de
spelersselectie en **vóór** het inlezen en reconstrueren van de replay
controleert de extractor beide definitieve bestemmingen met
`lstatSync`. Dit weigert óók dangling symlinks en maakt geen
bestemmingsbestand aan. De definitieve `linkSync` blijft nodig:
een ander proces kan immers ná de controle alsnog de naam innemen.

Ook het opruimen bij een fout is verbeterd. Een `WriteStream` kan
asynchroon zijn bestand nog openen wanneer een replayfout optreedt.
Vroeg verwijderen vóór de stream werkelijk `close` uitzendt kon
daardoor alsnog een onbedoeld privé-temporair bestand achterlaten.
De foutafhandeling vernietigt nu eerst de stromen, wacht hun
output-`close` af en verwijdert daarna uitsluitend de eigen
tijdelijke shard en eventuele tijdelijke receipt. Reeds gepubliceerde
eindbestanden blijven ongemoeid. Een mislukte extractie meldt
niet alsnog succes en start geen modeltraining of serviceherstel.
