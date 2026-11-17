# Coach-brug voor het trainingsdashboard

Kleine HTTP-dienst (Flask + gunicorn) die tekst voor een AI-coach inzet en het
antwoord teruggeeft. Het trainingsdashboard
([trainingsdashboard](https://github.com/FireStarter75/trainingsdashboard),
draait op dash.veldhuizen.us) praat via een bearer-token met deze brug; de
brug roept headless de [Vibe CLI](https://github.com/mistralai/mistral-vibe)
aan (Mistral `mistral-medium-3.5`) en geeft het antwoord terug.

Deze dienst staat met opzet in een eigen container. Hij ziet de code en de
data van het dashboard niet — geen enkele mount daarvan — en kan er dus ook
niets aan veranderen. Hij doet precies één ding: een prompt doorgeven en het
antwoord teruggeven.

## Waarom deze container zo kaal is

De coach kan niets aan het dashboard, de containers of de data veranderen.
Dat zit niet alleen in zijn prompt, maar in de bouw:

1. **Headless zonder goedkeuring.** De CLI draait met `-p` (programmatic
   mode): goedkeuring is onmogelijk, dus élke tool-aanroep wordt door het
   goedkeuringsbeleid geweigerd in plaats van uitgevoerd. Getest met
   vijandige schrijf-, shell- en netwerkpogingen: nul bijeffecten.
2. **Vervangen systeemprompt.** `--system-prompt-file` vervangt de
   systeemprompt van Vibe volledig door de briefing van het dashboard; de
   brug voegt daar een afweerzin aan toe (geen gereedschap, alleen tekst).
3. **Begrensde lus en kosten.** `--max-turns` (hoogstens enkele
   geweigerde toolpogingen + het antwoord) en `--max-price` (kostenplafond
   per vraag, `COACH_MAX_BUDGET_USD`).
4. **Gesloten omgeving.** De eigen `VIBE_HOME` (vibe-home/) bevat een
   minimale config: geen skills, agents, plugins of MCP-servers, geen
   update-checks of telemetrie, en session-logging staat uit. De werkmap is
   een lege `/work` buiten elke vertrouwde map.
5. **Geen netwerk naar binnen.** De container hangt alleen aan het
   docker-netwerk `garmin-net` en is alleen bereikbaar voor het dashboard,
   met een token uit `.env`.

Alle gevolgen van wat de coach zegt (een training loggen, een week
bijstellen) voert het dashboard zelf uit, pas na een klik van de gebruiker,
tegen een vaste lijst van vijf soorten acties. Zie `app/coach.py` in het
dashboard-repo.

## Bestanden

| Bestand | Wat |
|---|---|
| `coach/server.py` | de brug: Flask-dienst die de CLI aanroept |
| `Dockerfile` | python:3.12-slim + `mistral-vibe` uit requirements.txt |
| `start.sh` | bouwt het docker-netwerk en start de container |
| `rebuild.sh` | image herbouwen na codewijzigingen |
| `vibe-home/config.toml` | de gesloten Vibe-config die in de container gemount wordt |

## Starten

1. Vul `.env` (kopieer het formaat):

       COACH_TOKEN=<willekeurige lange tekst>
       MISTRAL_API_KEY=<Mistral API-key>

2. Start de brug, daarna het dashboard:

       bash start.sh                          # of eerst: bash rebuild.sh
       bash /mnt/user/appdata/garmin-dashboard/start.sh

De API-key staat alleen in `.env` en gaat alleen als omgevingsvariabele naar
het CLI-kinderproces. `.env` staat niet in git.
