"""Coach-brug: zet tekst voor Vibe in, krijgt tekst terug.

Deze dienst staat met opzet in een eigen container. Hij ziet de code en de
data van het dashboard niet - geen enkele mount daarvan - en kan er dus ook
niets aan veranderen. Hij doet precies een ding: een prompt doorgeven aan de
Vibe CLI (headless) en het antwoord teruggeven.

De CLI wordt zo dichtgezet dat er niets uitgevoerd kan worden:

  headless (`-p`)           geen enkele goedkeuring mogelijk: élke
                            tool-aanroep wordt door het goedkeuringsbeleid
                            geweigerd in plaats van uitgevoerd. Dit is
                            getest met vijandige prompts (schrijf-, shell- en
                            netwerkpogingen): nul bijeffecten, elke poging
                            kwam terug als "Tool call denied".
  --max-turns 1..4          hoogstens enkele geweigerde toolpogingen + het
                            antwoord; geen echte agent-lus
  --max-price               harde kostenplafonnelijkheid per vraag
  --system-prompt-file      het eigen systeemprompt van Vibe wordt volledig
                            vervangen door de briefing van het dashboard
  VIBE_HOME                 lege, gesloten configuratie (geen skills, agents,
                            plugins, MCP-servers; session-logging uit)

De werkmap is een lege /work buiten elke vertrouwde map, dus ook
projectconfiguratie wordt nooit geladen. Wat er na al dit nog kan, is tekst
produceren. Alle gevolgen daarvan (een training loggen, een week bijstellen)
worden door het dashboard zelf uitgevoerd, pas na een klik van de gebruiker.
Zie coach.py daar.
"""
import json
import os
import re
import subprocess
import tempfile
import threading
import time

from flask import Flask, jsonify, request

app = Flask(__name__)

TOKEN = os.environ.get("COACH_TOKEN", "")
VIBE_BIN = os.environ.get("VIBE_BIN", "/usr/local/bin/vibe")
MODEL = os.environ.get("COACH_MODEL", "mistral-medium-3.5")
# Witte lijst van model-aliases: geen vrije tekst gaat naar de CLI.
ALLOWED_MODELS = tuple(
    m for m in os.environ.get("COACH_MODEL_ALLOWED", MODEL).split(",") if m
)
TIMEOUT = int(os.environ.get("COACH_TIMEOUT", "150"))
MAX_PROMPT = int(os.environ.get("COACH_MAX_PROMPT", "120000"))
MAX_SYSTEM = int(os.environ.get("COACH_MAX_SYSTEM", "120000"))
BUDGET = os.environ.get("COACH_MAX_BUDGET_USD", "0.50")
WORKDIR = os.environ.get("COACH_WORKDIR", "/work")
_DENIED = re.compile(r"^Tool call denied: (\S+)", re.M)

# Twee gesprekken tegelijk is genoeg voor een dashboard met een gebruiker, en
# houdt het aantal CLI-processen (en daarmee het geheugen) voorspelbaar.
_gate = threading.Semaphore(int(os.environ.get("COACH_CONCURRENCY", "2")))

_stats = {"calls": 0, "errors": 0, "cost_usd": 0.0, "last_ms": None, "last_error": None}


def _authorized():
    if not TOKEN:
        return False
    got = request.headers.get("Authorization", "")
    return got.startswith("Bearer ") and got[7:] == TOKEN


def _child_env():
    """Minimale omgeving voor de CLI.

    Het eigen token en de overige instellingen gaan niet mee: het kind heeft ze
    niet nodig, en wat niet in de omgeving staat kan ook niet in een antwoord
    belanden. Alles wat de CLI nodig heeft is de Mistral API-key en een eigen
    VIBE_HOME (gemount, gesloten config; bestaat altijd).
    """
    keep = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/root"),
        "TZ": os.environ.get("TZ", "Europe/Amsterdam"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        # Vibe schrijft hier caching/analytics (allebei uit via config.toml);
        # expliciet zetten zodat het nooit ergens anders terechtkomt.
        "VIBE_HOME": os.environ.get("VIBE_HOME", os.path.join(os.environ.get("HOME", "/root"), ".vibe")),
    }
    if os.environ.get("MISTRAL_API_KEY"):
        keep["MISTRAL_API_KEY"] = os.environ["MISTRAL_API_KEY"]
    return keep


def _argv(system_file, model, max_turns):
    argv = [
        VIBE_BIN, "-p",
        # De prompt leest de CLI van stdin; --system-prompt-file vervangt
        # Vibe's eigen systeemprompt volledig door de briefing van het
        # dashboard. Headless zonder goedkeuring: elke tool-aanroep wordt
        # geweigerd (zie docstring); --max-turns en --max-price begrenzen de
        # lus en de kosten.
        "--system-prompt-file", system_file,
        "--model", model,
        "--max-turns", str(max_turns),
        "--max-price", str(BUDGET),
        "--output", "json",
    ]
    return argv


@app.get("/health")
def health():
    if not _authorized():
        return jsonify({"error": "geen toegang"}), 401
    ok = os.access(VIBE_BIN, os.X_OK)
    ver = None
    if ok:
        try:
            ver = subprocess.run([VIBE_BIN, "--version"], capture_output=True, text=True,
                                 timeout=30, env=_child_env()).stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            ok, ver = False, str(e)[:120]
    creds = bool(os.environ.get("MISTRAL_API_KEY"))
    return jsonify({"ok": bool(ok), "cli": ver, "model": MODEL,
                    "credentials_present": creds,
                    "stats": _stats})


@app.post("/ask")
def ask():
    """Body: {"system": "...", "prompt": "...", "model": "mistral-medium-3.5", "max_turns": 1}"""
    if not _authorized():
        return jsonify({"error": "geen toegang"}), 401

    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()
    system = str(body.get("system") or "").strip()
    model = str(body.get("model") or MODEL)
    # Standaard wat extra slag: als het model beurten verspilt aan geweigerde
    # tool-aanroepen, krijgt het alsnog de kans zijn tekst te geven. Geweigerde
    # aanroepen kosten niets; --max-price blijft het bedragplafond.
    max_turns = max(1, min(4, int(body.get("max_turns") or 4)))

    if not prompt:
        return jsonify({"error": "lege prompt"}), 400
    if len(prompt) > MAX_PROMPT or len(system) > MAX_SYSTEM:
        return jsonify({"error": "prompt te groot"}), 413
    if not system:
        return jsonify({"error": "systeemprompt verplicht"}), 400
    # Alleen aliassen uit de witte lijst; geen vrije tekst naar de CLI.
    if model not in ALLOWED_MODELS:
        return jsonify({"error": "onbekend model"}), 400

    if not _gate.acquire(timeout=20):
        return jsonify({"error": "coach is bezig, probeer het opnieuw"}), 503

    t0 = time.time()
    tmp = None
    try:
        # Het systeemprompt gaat als bestand mee: --system-prompt-file
        # vervangt de systeemprompt van Vibe volledig.
        fd, tmp = tempfile.mkstemp(prefix="coach-system-", suffix=".md", dir="/tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(system)
            # Afweer in de diepte: ook als de briefing het niet zegt, weet het
            # model dat gereedschap hier niet bestaat.
            fh.write("\n\nJe hebt geen gereedschap: er is geen shell, geen "
                     "bestandstoegang, niets. Probeer dat dus niet en "
                     "antwoord uitsluitend met tekst.\n")
        try:
            proc = subprocess.run(
                _argv(tmp, model, max_turns),
                input=prompt, capture_output=True, text=True,
                timeout=TIMEOUT, cwd=WORKDIR, env=_child_env(),
            )
        except subprocess.TimeoutExpired:
            _stats["errors"] += 1
            _stats["last_error"] = "timeout"
            return jsonify({"error": "de coach deed er te lang over"}), 504
        except OSError as e:
            _stats["errors"] += 1
            _stats["last_error"] = str(e)[:200]
            return jsonify({"error": "kon de coach niet starten: %s" % e}), 500
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        _gate.release()

    ms = int((time.time() - t0) * 1000)
    _stats["calls"] += 1
    _stats["last_ms"] = ms

    # Vibe slaat tool-aanroepgeweigerd-calls op stderr ("Tool call denied: …").
    # Elke poging is een geraakte grens: niets wordt uitgevoerd, maar we
    # loggen het, net als voorheen.
    denials = _DENIED.findall(proc.stderr or "")
    if denials:
        app.logger.warning("coach probeerde gereedschap te gebruiken (geweigerd): %s",
                           denials[:10])

    # De headless run mag non-zero eindigen (bijv. "turn_limit") en toch al
    # een antwoord opgeleverd hebben; het transcript staat gewoon in stdout.
    try:
        entries = json.loads(proc.stdout)
    except json.JSONDecodeError:
        _stats["errors"] += 1
        _stats["last_error"] = (proc.stderr or "")[:300]
        app.logger.warning("vibe faalde (%s): %s", proc.returncode, (proc.stderr or "")[:400])
        return jsonify({"error": "de coach gaf een fout: %s" % (proc.stderr or "onleesbaar antwoord")[:200]}), 502

    text = ""
    for entry in entries if isinstance(entries, list) else []:
        if entry.get("type") == "message" and entry.get("role") == "assistant":
            text = "".join(
                part.get("text", "") for part in (entry.get("content") or [])
                if isinstance(part, dict) and part.get("type") == "text"
            )
    if not text.strip():
        _stats["errors"] += 1
        _stats["last_error"] = str(proc.stderr or "leeg antwoord")[:300]
        return jsonify({"error": str(proc.stderr or "de coach gaf een leeg antwoord")[:200]}), 502

    return jsonify({
        "ok": True,
        "text": text,
        "cost_usd": None,      # Vibe-rapportage heeft geen kosten per vraag
        "ms": ms,
        "model": model,
        "denials": len(denials),
        "usage": None,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
