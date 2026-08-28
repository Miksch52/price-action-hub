#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Katalysator-Layer fuer die Top-Setups (MTS-Automatisierungs-Fahrplan,
Vorschlag 1, 2026-08-28).

Schliesst eine konkrete Luecke: keine der drei Screening-Engines (Signal-Hub,
Price-Action-Hub, Rotation-Dashboard) sagt jemals WARUM ein Wert gerade
technisch reif ist - Minervini/O'Neil verlangen aber einen fundamentalen
Grund hinter der besten Basis (Zahlen, Auftrag, Zulassung, Analysten-Upgrade,
Indexaufnahme, Uebernahme). Dieses Modul holt fuer jedes aktuelle Top-Setup
per Perplexity-Sonar-API eine kurze Einordnung nach.

Verifizierte Schnittstelle (WebSearch/WebFetch, 2026-08-28, siehe
docs.perplexity.ai/docs/sonar/openai-compatibility):
  Endpunkt:  POST https://api.perplexity.ai/chat/completions
             (OpenAI-kompatibler Alias von /v1/sonar - breiter dokumentiert
             und von jedem existierenden Beispiel im Oekosystem genutzt,
             deshalb hier bewusst gewaehlt statt des neueren /v1/sonar)
  Auth:      Header "Authorization: Bearer <PERPLEXITY_API_KEY>"
  Modell:    "sonar" (NICHT "sonar-pro" - Output kostet dort das 15-fache,
             fuer eine Zwei-Satz-Einordnung mit Quellenangabe reicht die
             Basis-Stufe; siehe Fahrplan-Artefakt Abschnitt 07)
  response_format: {"type":"json_schema","json_schema":{"name":...,"schema":{...}}}
  search_recency_filter: "week" - sonst begruendet das Modell einen
             heutigen Breakout mit einer Meldung aus dem Fruehjahr.
  search_domain_filter: kleine Zulassung auf seriöse Finanzquellen -
             verbessert Qualitaet UND verkleinert die Angriffsflaeche fuer
             untergeschobene Inhalte in der Rueckantwort.

Kosten/Rhythmus: der Kostentreiber ist die Request-Gebuehr (~5 $ je 1000
Anfragen), nicht die Token - deshalb hoechstens EINMAL TAEGLICH (Gate ueber
KATALYSATOR_STATE), nicht in allen vier Cloud-Slots. Ohne gesetztes
PERPLEXITY_API_KEY (GitHub-Repo-Secret im signal-hub-Repo, analog
DEPLOY_TRIGGER_TOKEN) scheitert der Schritt bewusst still - das Feature ist
rein additiv wie top_setups.py/hebel_backtest.py und darf die Pipeline nie
blockieren.

Sicherheit: der zurueckkommende Text ist FREMDINHALT AUS DEM NETZ. Er wird
laenge-begrenzt, nach einer festen Klassen-Liste geprueft und ausschliesslich
als Anzeigetext gespeichert - nie als Anweisung interpretiert, nie in einen
weiteren Prompt zurueckgespeist. Das Frontend (index.html) rendert ihn ueber
eine escapte Zeichenkette, nicht ueber rohes HTML.

Geht bewusst NICHT sofort in den Score ein (siehe CLAUDE.md-Backtest-Pflicht)
- katalysator_backtest.py sammelt zunaechst nur eine unverzerrte Forward-
Test-Kohorte "mit Katalysator" vs. "ohne Katalysator".

Test:  PERPLEXITY_API_KEY=... python3 src/katalysator.py --lauf   # sofort, ignoriert das Tages-Gate
       python3 src/katalysator.py --status                        # zeigt Gate-Zustand, ruft nichts ab
"""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import pfade

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

API_URL = "https://api.perplexity.ai/chat/completions"
MODELL = "sonar"
MAX_TICKER = 15          # Kostendeckel je Lauf, unabhaengig von top_setups.py::MAX_SETUPS
MAX_TEXT_LAENGE = 280     # Fremdinhalt: harte Laengenbegrenzung
TIMEOUT_SEK = 40          # erste Anfrage mit neuem JSON-Schema kann 10-30s brauchen

KLASSEN = ["zahlen", "guidance", "auftrag", "zulassung", "analyst", "index", "uebernahme", "keiner"]

# Bewusst klein und auf etablierte Finanzquellen begrenzt - kein Versuch,
# jede denkbare Landessprache/Nische abzudecken (Best-Effort statt Vollstaendigkeit).
DOMAIN_FILTER = [
    "reuters.com", "bloomberg.com", "cnbc.com", "marketwatch.com",
    "finanzen.net", "boerse-online.de", "handelsblatt.com",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "klasse": {"type": "string", "enum": KLASSEN},
        "text": {"type": "string"},
    },
    "required": ["klasse", "text"],
}


def _heute_str():
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Tages-Gate
# ---------------------------------------------------------------------------
def _state_load():
    if os.path.exists(pfade.KATALYSATOR_STATE):
        try:
            return json.load(open(pfade.KATALYSATOR_STATE, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _state_save(s):
    with open(pfade.KATALYSATOR_STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def _heute_schon_gelaufen():
    return _state_load().get("letzter_lauf") == _heute_str()


# ---------------------------------------------------------------------------
# API-Aufruf
# ---------------------------------------------------------------------------
def _frage(ticker, name, markt):
    ort = "USA" if markt == "USA" else "Europa" if markt == "Europa" else (markt or "")
    inhalt = (
        f"Aktie {ticker} ({name}, {ort}). Gab es in den letzten 7 Tagen einen konkreten "
        f"fundamentalen Anlass fuer eine staerkere Kursbewegung (Quartalszahlen, "
        f"angehobene/gesenkte Prognose, Grossauftrag, behoerdliche Zulassung, "
        f"Analysten-Up-/Downgrade, Indexaufnahme, Uebernahme/Fusion)? Antworte knapp "
        f"auf Deutsch in maximal zwei Saetzen. Wenn kein solcher Anlass auffindbar ist, "
        f"setze klasse auf 'keiner' und text auf einen kurzen Hinweis, dass keiner "
        f"gefunden wurde."
    )
    body = {
        "model": MODELL,
        "messages": [{"role": "user", "content": inhalt}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "katalysator", "schema": SCHEMA},
        },
        "search_recency_filter": "week",
        "search_domain_filter": DOMAIN_FILTER,
        "max_tokens": 300,
    }
    req = urllib.request.Request(
        API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={
            "Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEK, context=SSL_CTX) as r:
        antwort = json.load(r)
    inhalt_text = antwort["choices"][0]["message"]["content"]
    geparst = json.loads(inhalt_text)
    klasse = geparst.get("klasse")
    if klasse not in KLASSEN:
        klasse = "keiner"
    text = str(geparst.get("text") or "").strip()[:MAX_TEXT_LAENGE]
    quellen = [str(u) for u in (antwort.get("citations") or [])][:3]
    return {"klasse": klasse, "text": text, "quellen": quellen}


def _hole_katalysator(ticker, name, markt, versuche=2):
    letzter_fehler = None
    for versuch in range(versuche):
        try:
            return _frage(ticker, name, markt)
        except urllib.error.HTTPError as ex:
            letzter_fehler = f"HTTP {ex.code}"
            if ex.code in (401, 403):
                break   # falscher/fehlender Key - weitere Versuche sinnlos
        except Exception as ex:
            letzter_fehler = str(ex)
        time.sleep(1.5)
    print(f"  Katalysator {ticker}: uebersprungen ({letzter_fehler}).")
    return None


# ---------------------------------------------------------------------------
# Lauf + Schreiben
# ---------------------------------------------------------------------------
def _schreibe(ergebnis, stand):
    with open(pfade.KATALYSATOR_JSON, "w", encoding="utf-8") as f:
        json.dump({"erstellt": stand, "modell": MODELL, "katalysatoren": ergebnis},
                   f, ensure_ascii=False, indent=1)
    with open(pfade.KATALYSATOR_JS, "w", encoding="utf-8") as f:
        f.write("window.TOP_SETUPS_KATALYSATOR = ")
        json.dump(ergebnis, f, ensure_ascii=False)
        f.write(";")


def lauf():
    """Fuehrt den API-Lauf JETZT aus, unabhaengig vom Tages-Gate. Gibt das
    Ergebnis-Dict (ticker -> {klasse,text,quellen}) zurueck, {} bei Abbruch."""
    if "PERPLEXITY_API_KEY" not in os.environ:
        print("Katalysator-Layer: kein PERPLEXITY_API_KEY gesetzt - uebersprungen.")
        return {}
    if not os.path.exists(pfade.SIGNAL_HUB_TOP_SETUPS_JSON):
        print("Katalysator-Layer: keine top_setups.json - uebersprungen.")
        return {}
    daten = json.load(open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, encoding="utf-8"))
    setups = (daten.get("setups") or [])[:MAX_TICKER]
    if not setups:
        print("Katalysator-Layer: keine Top-Setups heute - nichts abzufragen.")
        return {}

    stand = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ergebnis = {}
    for s in setups:
        ticker = s.get("ticker")
        if not ticker:
            continue
        r = _hole_katalysator(ticker, s.get("name") or "", s.get("markt") or "")
        if r:
            r["stand"] = stand
            ergebnis[ticker] = r
        time.sleep(0.3)
    _schreibe(ergebnis, stand)
    _state_save({"letzter_lauf": _heute_str(), "anzahl": len(ergebnis)})
    print(f"Katalysator-Layer: {len(ergebnis)}/{len(setups)} Ticker eingeordnet -> {pfade.KATALYSATOR_JSON}")
    return ergebnis


def taeglich_falls_faellig():
    """Bequemer Einstiegspunkt fuer run.py: laeuft hoechstens einmal pro
    Kalendertag (Kostengrund, siehe Modul-Docstring). Scheitert nie hart."""
    if _heute_schon_gelaufen():
        print("Katalysator-Layer: heute bereits gelaufen - uebersprungen.")
        return {}
    try:
        return lauf()
    except Exception as ex:
        print(f"Katalysator-Layer: unerwarteter Fehler, uebersprungen ({ex}).")
        return {}


def main():
    args = sys.argv[1:]
    if "--status" in args:
        st = _state_load()
        print(f"Letzter Lauf: {st.get('letzter_lauf', 'noch nie')} "
              f"({st.get('anzahl', 0)} Ticker) · Key gesetzt: "
              f"{'ja' if 'PERPLEXITY_API_KEY' in os.environ else 'nein'}")
        return
    if "--lauf" in args:
        lauf()
        return
    print("Nutzung: katalysator.py --lauf | --status")


if __name__ == "__main__":
    main()
