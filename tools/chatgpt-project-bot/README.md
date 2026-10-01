# ChatGPT Project Bot — native Linux desktop

Deze versie bedient een **zelf geopende, normale browser via Linux AT-SPI**.
Geen Playwright, WebDriver, browser-debugpoort, DOM-injectie of OpenAI API.
De bot opent, sluit en navigeert geen browser. xdotool is niet nodig: tekst en
knoppen worden rechtstreeks via accessibility bediend, zonder globale toetsen.

## Installeren of updaten (Debian/Ubuntu)

Voer als je normale desktopgebruiker uit (niet met sudo voor het hele commando):

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/Zennay/Haxlab/main/install-chatgpt-project-bot.sh)"
```

De installer gebruikt sudo voor apt, stopt de oude service/timer en vervangt de
bot. Bestaande `chat_url`, `prompt` en native labels blijven behouden; een kopie
van de oude config staat in `config.before-desktop.json`. Controle/timeout worden
op 60 seconden / 20 minuten gezet. Oude browserprofielen en .venv blijven op
schijf, maar worden niet gebruikt. De service blijft gestopt totdat je hem aan
de desktop koppelt. Ook na een update: voer `login.sh` opnieuw uit.

## Eerste start en opnieuw koppelen

1. Gebruik een actieve Linux-desktop (bijvoorbeeld KDE via xRDP/NoMachine).
2. Open Firefox of Chrome **zelf**, log handmatig in en open de gewenste chat.
3. Zet gewone **Chat** en **High/Hoog** handmatig aan. Los eventuele robotcheck
   zelf op. Laat de doelchat als zichtbare tab geopend en de desktop actief.
4. Stel in `~/.local/share/chatgpt-project-bot/config.json` de exacte
   `https://chatgpt.com/c/...` URL in. Een bestaande URL blijft bij updates staan.
5. Voer **in een terminal binnen die desktop** uit:

   ```bash
   ~/.local/share/chatgpt-project-bot/login.sh
   ```

6. Bevestig met `CHAT HIGH`. De bot controleert de toegankelijke chat en
   High-bediening en start daarna de systemd **user service**.

De eerste controle verstuurt meteen de vervolgprompt als de chat niet bezig is.
De bot wijzigt geen model-/reasoninginstellingen en selecteert nooit Work.
De keuze voor gewone Chat wordt handmatig bevestigd; High moet elke controle als
herkenbare knop zichtbaar zijn. Als ChatGPT de instelling verbergt of verandert,
pauzeert de bot. Hij kan niet garanderen dat de website instellingen bewaart.

## Gedrag

- Iedere 60 seconden inspectie (lange accessibility-aanroepen kunnen vertragen).
- Stop-knop zichtbaar: wachten; verdwenen: volgende prompt op de eerstvolgende
  controle, uiterlijk circa 60 seconden later.
- Na 20 minuten gemeten generatie: Stop bedienen, maximaal 15 seconden bevestigen
  dat Stop verdwijnt, daarna vervolgprompt. Bij al lopende generatie tijdens
  koppelen begint de timer bij de eerste observatie.
- Geen blinde verzending als Stop mislukt, de URL niet exact klopt, meerdere
  doelvensters open zijn, High ontbreekt of invoer niet herkenbaar is.
- Bestaande concepttekst wordt nooit overschreven.
- Herkende captcha/login/fout/limiet: pauzeren, geen oplossing of omzeiling.
  Detectie is afhankelijk van toegankelijke labels; onbekende UI kan blokkeren.
- Intentie wordt vóór elke actie opgeslagen. Bij een crash of onbevestigde
  verzending/Stop blijft de bot gepauzeerd. Controleer de chat, ruim zo nodig het
  concept op en voer `login.sh` opnieuw uit. Dit voorkomt automatisch dubbel sturen.
- Een zeer kort antwoord waarbij Stop nooit wordt waargenomen geldt als
  onbevestigde verzending en vraagt eveneens handmatige controle.

## Accessibility instellen en diagnosticeren

De browser moet de document-URL, berichtinvoer, High- en Stop/Send-knoppen via
AT-SPI beschikbaar stellen. Dit verschilt per browser/distributie en ChatGPT UI.
Deze versie heeft geautomatiseerde controller-tests; echte browsercompatibiliteit
moet op je Linux-desktop worden gecontroleerd. Firefox is een goede eerste test.
Schakel zo nodig desktop-accessibility in, herstart de browser **zelf** en probeer
opnieuw. Als Firefox accessibility uitgeschakeld heeft, controleer handmatig
`accessibility.force_disabled` in `about:config` (0 = standaard ingeschakeld).

Stop de service en toon uitsluitend de toegankelijke bedieninglabels:

```bash
systemctl --user stop chatgpt-project-bot.service
~/.local/share/chatgpt-project-bot/run.sh --inspect
```

Pas indien nodig `composer_names`, `high_names`, `stop_names` en `send_names` in
config.json aan de **exacte** labels aan. Kies alleen de daadwerkelijke knoppen
of berichtinvoer; gebruik nooit algemene tekst uit een antwoord als herkenning.
Als de browser geen document-URL of EditableText-interface aanbiedt, werkt deze
versie daar niet en blijft hij veilig gestopt. Er is geen coördinatenfallback.

## Service en logs

```bash
systemctl --user status chatgpt-project-bot.service
journalctl --user -u chatgpt-project-bot.service -f
~/.local/share/chatgpt-project-bot/run.sh --status
systemctl --user stop chatgpt-project-bot.service
```

De service herstart bij een procesfout, maar is bewust niet voor headless boot
ingeschakeld. Na reboot of een nieuwe desktoplogin open je browser/chat opnieuw
en voer je `login.sh` uit. Linger maakt geen grafische sessie. Het wegvallen van
RDP is alleen toegestaan als de desktop en browser actief blijven. Minimaliseren,
andere tabs of een vergrendelde desktop kunnen de bot laten pauzeren.

Status staat in `desktop-state.json` (los van legacy `state.json`). Logs gaan naar
journald. De installer vereist apt en een werkende systemd user manager.
`CHATGPT_BOT_DIR` kan een ander absoluut installatiepad kiezen zonder spaties.

## Tests

```bash
python3 -m unittest discover -s tools/chatgpt-project-bot/tests -v
bash -n install-chatgpt-project-bot.sh tools/chatgpt-project-bot/login.sh
```

Acceptatie op Linux: test eerst met een onbelangrijke chat; controleer een gewone
volgende beurt, High wegzetten (moet pauzeren), verkeerde tab, een concept en een
robotcheck/login. Test de watchdog eventueel tijdelijk met
`force_after_minutes: 1`, en zet daarna terug op 20. Verifieer in de logs dat Stop
bevestigd wordt vóór de volgende prompt en dat herstarten geen dubbele beurt stuurt.
