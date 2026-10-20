# Canonical raw source receipt paths — issue #407

The HaxLab source bundle producer inventories `.hbr2` (case-insensitive extension) and `.json` import evidence recursively under the selected immutable export root. On POSIX, a literal backslash is a valid filename/ancestor character, but the canonical downstream source receipt verifier **rejects all `\\` path characters** as ambiguous/noncanonical. Previously the producer could successfully hash and emit an impossible-to-verify receipt.

The real producer now **rejects an importer-relevant path that contains any literal backslash**, including when the backslash belongs to a nested parent directory, before hashing that file or constructing the emitted canonical bundle. The stable reason is `source_path_noncanonical_backslash`, without the raw filename. The rule applies only after recognizing an importer-relevant extension; unrelated non-source filenames are not promoted into the manifest. Ordinary canonical relative POSIX paths, case-insensitive HBR2 recognition and existing file identity / no-follow / provenance checks remain unchanged.

This source-only repair is deliberately disjoint from `src/haxlab/ingestion/source_receipt_verifier.py`, CLI, ingestion/publisher, runtime, training, evaluation and model/champion promotion. A project must not try to work around the verifier rule by escaping or normalizing away source path bytes; that would misstate evidence identity.

No QA files, suites or workflow configs were authored or manually invoked. Existing exact-head CI, independent reviewer acceptance, branch protection and authorized production handoff remain mandatory before merge or runtime assertions. See [#407](https://github.com/Zennay/Haxlab/issues/407).

## 20 oktober · Verifieerbare importbron bij lastige POSIX-bestandsnamen en grote exports

Dezelfde bron-PR weigert nu ook daadwerkelijk **niet naar canonieke UTF-8
encodeerbare** importbestandsnamen. Linux kan zulke namen via Python
`surrogateescape` als tekens in `0xD800–0xDFFF` aanbieden. Voorheen
liep het genereren van het canonieke JSON-receipt daarop onverwacht vast
in `encode("utf-8")`, zonder stabiele importfout. Een importer-relevant
`.hbr2` of `.json`-bestand met een dergelijke naam, óók onder een
oudermap met ondecodeerbare bytes, geeft nu de vaste fout
`source_path_noncanonical_unicode`. Er wordt niets hernoemd, gerepareerd
of stilzwijgend weggelaten. Niet-relevante bronbestanden worden niet alsnog
in het importbewijs opgenomen.

De bestaande verifier kan maximaal **32 MiB canonieke receipt-JSON**
verwerken. De producent controleert nu de werkelijk geserialiseerde
uiteindelijke JSON ná toevoeging van de SHA-256-zelfdigest en weigert
een grotere manifest met `source_receipt_too_large`. Hij maakt
geen afgesneden gedeeltelijk manifest of pseudo-digest dat downstream
nooit geldig kan zijn. Broninhoud, directory-provenance en afgeschermde
HaxLab-training worden niet aangeraakt.

Dit is **broncode op bestaande [PR #581](https://github.com/Zennay/Haxlab/pull/581)**,
niet op main of live bronarchief. Bestaande exacte-head CI,
onafhankelijke review, release-autorisatie en bronintegriteitsgates
blijven gelden. Geen QA-/regressietests geschreven of gestart.

## 20 oktober · Privé receipt-bestand voor grote externe bronarchieven

Dezelfde bestaande PR biedt nu een optionele, echte CLI-uitvoer naar een **nieuw**
privébestand, bedoeld voor een groot extern replay-/Discord-bronarchief:

```bash
mkdir -m 700 -p /home/ubuntu/private-receipts
python -m haxlab.ingestion.source_bundle_receipt \
  /media/replays/immutable-export \
  --output /home/ubuntu/private-receipts/source-20261020.json
```

De opgegeven uitvoermap moet **al bestaan**, mag geen symlink-ancestor
of groeps-/wereldschrijfrechten bevatten, en moet de nieuwe privébestanden
kunnen opslaan. Anders faalt dit pad met `source_receipt_output_parent_shared`
of `source_receipt_output_parent_invalid` in plaats van in een gedeelde map
te publiceren. De schrijver opent de ontvangende map via vastgepinde no-follow
directorydescriptors en schrijft de canonieke bytes eerst volledig naar
een nieuw `0600` staging-bestand. Na `fsync` van het staging-bestand wordt
de definitieve `.json`-bestandsnaam **atomair create-only** gepubliceerd via
een hardlink binnen dezelfde directory. Een bestaande naam (ook een symlink)
wordt nooit overschreven. Het tijdelijke bestand wordt uitsluitend verwijderd
wanneer de inode nog aantoonbaar van deze run is, en de directory wordt
gesynchroniseerd. Kan het filesysteem geen atomische hardlink maken, dan
faalt de export expliciet zonder onveilig `rename/replace`-alternatief.
Een voortijdig gestopt proces mag hoogstens een privé staging-bestand
achterlaten, niet een halfgeschreven definitieve receipt. De doelbestemming moet **buiten** de bronroot
liggen; anders zou een toekomstige scan zijn eigen receipt als importbron
meenemen. De producer handhaaft het 32 MiB-maximaal-formaat van de bestaande
verifier.

Met `--output` geeft stdout alleen een beperkte succes-samenvatting met
file-/typeaantallen, totale bytes en receipt-SHA256: geen bronpaden, geen
inhoud, geen privé-uitvoerpad. Zonder `--output` blijft het bestaande CLI-
contract intact: de volledige receipt wordt zoals voorheen op stdout
geprint. De integriteitscontrole, importbronbestanden, hashberekening,
canonicalisatie en downstream verifier zijn niet veranderd. Er wordt geen
externe opslag automatisch gemount, aangemaakt, opgeruimd of gemigreerd.
Echte CI, onafhankelijk review en releaseautorisatie blijven verplicht.

Het finale bestandsdescriptoorgegeven resultaat wordt nog vergeleken met de
privé staging-inode; afwijkende identiteit of onverwachte leesrechten leveren
een expliciete fout, geen geslaagde publicatiemelding. Op een filesysteem
zonder hardlink-ondersteuning moet een ander privé-uitvoerpad worden gekozen;
er is bewust geen onveilige vervangende publicatieroute.
