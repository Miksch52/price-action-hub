#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Top-Setups-Aggregator (Startseiten-Panel "🏆 Top-Setups heute").

Reiner JSON-Join OHNE Cross-App-Python-Import (Entflechtung, CLAUDE.md: "Apps
nicht mergen" - erlaubt ist nur der Datenaustausch ueber die fertigen
Ausgabedateien): liest drei fertige Outputs per Pfad und schreibt eine
WINZIGE Zusammenfassung, die index.html laedt - statt der 11.8 MB signals.js
+ 5.7 MB priceaction.js auf der Startseite.

Quellen (alle liegen vor, weil dieser Schritt als LETZTER der Pipeline laeuft,
Price-Action-Hub/src/run.py nach dem PA-Scorer):
  Signal-Hub/data/signals.json          - score, tier, markt, marktregime, earnings,
                                           quellen.unabhaengig (Konfluenz unabhaengiger
                                           Scoring-Engines, siehe scorer.py::provider_group)
  Signal-Hub/data/pivot.json            - pivot_status (ARMED/BREAKOUT), qualitaet, pivot, stop
  Signal-Hub/data/pivot_backtest.json   - forward_realisiert je Status/Horizont (Win-Rate,
                                           n) aus dem unverzerrten Forward-Test, siehe
                                           pivot_backtest.py::evaluate - liegt im selben
                                           Artifact wie signals.json/pivot.json (signal-hub
                                           laeuft VOR diesem Job), kein Cross-Job-Problem.
  Price-Action-Hub/data/priceaction.json - pa_score

"Top-Setup" = die Schnittmenge, die man sonst ueber drei Dashboards manuell
kreuzen muesste: score >= kauf_kandidat UND pivot_status in {ARMED,BREAKOUT}
UND pa_score > 0. Das Markt-Regime je Markt und der naechste Earnings-Termin
werden PRO EINTRAG mitgeliefert (nicht hart gefiltert) - das Frontend
entscheidet ueber die Darstellung (gruenes Regime = aktiv; gelb/rot bzw.
Earnings-Sperre = mit Minervini-Hinweis), sonst waere das Panel an jedem
nicht-gruenen Tag leer. Bewusst config-frei (kein warn_tage-Lesen): das
Earnings-Fenster wendet das Frontend mit seiner eigenen warnTage()-Logik an,
damit Startseite und Signal-Hub-Dashboard nie widersprechen.

Konfluenz & Kern-Setups (seit 2026-08-17): zwei zusaetzliche, rein additive
Signale, die NICHT filtern (jedes bisherige Top-Setup bleibt drin), sondern
nur einordnen/priorisieren - Minervinis/O'Neils Grundprinzip "mehrere
unabhaengige Bestaetigungen schlagen ein einzelnes Signal":
  - quellen_unabhaengig: wie viele unabhaengige externe Engines (PDF/Finviz/
    Markets-360/Trend-Screener) denselben Ticker unabhaengig voneinander in
    den Signal-Hub-Trichter gespuelt haben (wiederverwendet scorer.py's
    bestehende quellen.unabhaengig-Liste, dort schon Basis fuer den "🔗 N×
    bestaetigt"-Badge im Signal-Hub-Dashboard).
  - kern_setup: True, wenn der Forward-Backtest fuer GENAU DIESEN Pivot-Status
    (ARMED/BREAKOUT) bei mindestens KERN_REIFE_N gereiften Picks eine Win-Rate
    >= KERN_WIN_SCHWELLE zeigt - zieht die historisch am besten bestaetigte
    Kohorte nach oben, statt sie in der Sortierung zufaellig zwischen
    schwaecheren Kohorten verschwinden zu lassen. Rotation-Dashboard
    (Gruppenfuehrerschaft) ist bewusst NICHT hier eingebaut: price-action-hub
    und rotation-dashboard laufen als PARALLELE Jobs (siehe pipeline.yml,
    "Diamant-Muster") - rotation.json existiert zum Zeitpunkt dieses Laufs
    schlicht noch nicht auf demselben Runner. Der Leader-Abgleich passiert
    stattdessen rein clientseitig in index.html (rotation.json ist winzig,
    kein Grund fuer einen Umweg ueber Artifacts/Jobreihenfolge).

Ausgabe: Signal-Hub/data/top_setups.json (+ .js fuer file:///Pages).

Push (seit 2026-08-09): jeder Lauf, der NEUE Ticker in die Schnittmenge
aufnimmt, schickt sie ueber denselben ntfy-Kanal wie die uebrigen drei
Push-Quellen (Signal-Hub-Digest, Pivot-Screener, Trading-Alarme - siehe
Wiki-Abschnitt "Push-Quellen im Ueberblick"). Bewusst OHNE Kopplung an die
vier festen Signal-Hub-Slots (07:30/15:00/17:00/21:30) - anders als der
Pivot-Screener-Push feuert dieser bei JEDEM Pipeline-Lauf, auch bei
manuellen "Cloud-Scan starten"-Klicks, weil Top-Setups die seltenste,
hoechstwertige Schnittmenge sind und ein zusaetzlicher Push hier nicht als
Spam gilt. Anti-Spam ueber Ticker-Zustand (siehe _neuzugaenge), nicht ueber
den Slot-Zeitplan. Eigenstaendiger Versand (kein Import von Signal-Hub/src/
notify.py) - konsequent zur bestehenden Entflechtung dieser Datei.
"""

import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

import pfade

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

_SH_DATA = os.path.join(pfade.REPO_ROOT, "Signal-Hub", "data")
SIGNALS = pfade.SIGNAL_HUB_SIGNALS_JSON
PIVOT = os.path.join(_SH_DATA, "pivot.json")
PIVOT_BACKTEST = os.path.join(_SH_DATA, "pivot_backtest.json")
OUT_JSON = os.path.join(_SH_DATA, "top_setups.json")
OUT_JS = os.path.join(_SH_DATA, "top_setups.js")

MAX_SETUPS = 40     # Deckel oberhalb des Frontend-Limits (seit 2026-08-17: 20
                     # direkt sichtbar, siehe index.html::renderTopSetups()::LIMIT) -
                     # laesst der "N weitere anzeigen"-Aufklappliste noch Raum,
                     # statt sie an Tagen mit vielen Treffern leerlaufen zu lassen.

# Kern-Setup-Schwellen (seit 2026-08-17, siehe Modul-Docstring "Konfluenz &
# Kern-Setups"). KERN_REIFE_N deckt sich mit der "reif genug"-Schwelle, die
# der Backtest selbst schon fuer Push-Benachrichtigungen nutzt (siehe
# pivot_backtest.py::SCHWELLE_PUSH) - unter dieser Stichprobengroesse gilt
# eine Win-Rate als noch zu verrauscht, um Setups danach hochzuziehen.
# KERN_WIN_SCHWELLE=60% liegt bewusst spuerbar unter dem bisher gemessenen
# ARMED-Wert (71%, n=83, Stand 2026-08-17) - ein fixer Puffer, damit die
# Schwelle nicht bei jeder kleinen Schwankung des Forward-Tests kippt.
KERN_REIFE_N = 8
KERN_WIN_SCHWELLE = 60.0
BACKTEST_HORIZONTE = ("12W", "8W", "4W")  # laengster zuerst: reifer = aussagekraeftiger

AMPEL_ICON = {"gruen": "🟢", "gelb": "🟡", "rot": "🔴"}
FLAGGE = {"USA": "US", "Europa": "EU"}


def _lade(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── Push (eigenstaendig, siehe Modul-Docstring) ──────────────────────────────
def _ntfy_settings():
    """ntfy-Server + Thema aus der Signal-Hub-Konfiguration lesen - reiner
    Datei-Read, kein Cross-App-Python-Import (gleiches Muster wie
    mts_alarms.py im Hauptrepo)."""
    try:
        with open(pfade.SIGNAL_HUB_CONFIG, encoding="utf-8") as f:
            b = json.load(f).get("benachrichtigung", {})
        thema = (b.get("ntfy_thema") or "").strip()
        server = (b.get("ntfy_server") or "https://ntfy.sh").strip()
        if thema and "NOCH" not in thema.upper():
            return server, thema
    except Exception:
        pass
    return None, None


def _sende_ntfy(titel, text, tags="trophy", prio="default"):
    server, thema = _ntfy_settings()
    if not thema:
        print("Top-Setups: kein ntfy-Thema in Signal-Hub/config.json - Push uebersprungen.")
        return False
    url = f"{server.rstrip('/')}/{thema}"
    req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST")
    req.add_header("Title", titel.encode("utf-8"))
    req.add_header("Tags", tags)
    req.add_header("Priority", prio)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as r:
            return 200 <= r.status < 300
    except Exception as e:
        print(f"Top-Setups: ntfy-Fehler: {e}")
        return False


def _state_load():
    try:
        with open(pfade.TOP_SETUPS_STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _state_save(s):
    with open(pfade.TOP_SETUPS_STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def _neuzugaenge(setups, state):
    """Ticker, die seit dem letzten Lauf NEU in die Top-Setups-Schnittmenge
    eingetreten sind (Anti-Spam-Vergleich gegen den letzten bekannten
    Zustand). Ein Dropout (z.B. aus den Top MAX_SETUPS gefallen) + spaeterer
    Wiedereinstieg zaehlt bewusst erneut als neu - gleiche Logik wie
    notify.py::baue_nachricht (nur_neue) und pivot_screener.py::neuzugaenge."""
    alt = set(state.get("ticker", []))
    neu = [s for s in setups if s["ticker"] not in alt]
    state["ticker"] = [s["ticker"] for s in setups]
    return neu


def _push(neu):
    if not neu:
        print("Top-Setups: keine neuen Setups seit letztem Lauf -> kein Push.")
        return
    zeilen = []
    for s in neu:
        icon = AMPEL_ICON.get(s.get("regime"), "⚪")
        status = "🚀" if s["pivot_status"] == "BREAKOUT" else "🎯"
        markt = FLAGGE.get(s.get("markt"), s.get("markt") or "")
        name = (s.get("name") or "")[:22]
        zeilen.append(f"{icon}{status} {s['ticker']}  {s['score']:.0f}  {name} ({markt})")
    titel = f"🏆 {len(neu)} neue{'s' if len(neu) == 1 else ''} Top-Setup{'' if len(neu) == 1 else 's'}"
    ok = _sende_ntfy(titel, "\n".join(zeilen))
    print(f"Top-Setups-Push {'gesendet' if ok else 'fehlgeschlagen (kein Thema oder ntfy-Fehler)'}: {titel}")


def _backtest_info(pivot_backtest, status):
    """Reifsten verfuegbaren Horizont fuer einen Pivot-Status liefern (oder
    None, wenn noch keiner KERN_REIFE_N gereifte Picks hat) - siehe
    Modul-Docstring "Konfluenz & Kern-Setups"."""
    block = ((pivot_backtest or {}).get("forward_realisiert") or {}).get(status) or {}
    for label in BACKTEST_HORIZONTE:
        s = block.get(label)
        if s and (s.get("n") or 0) >= KERN_REIFE_N and s.get("win") is not None:
            return {"win": s["win"], "n": s["n"], "horizont": label}
    return None


def schreibe():
    signals = _lade(SIGNALS)
    pivot = _lade(PIVOT)
    pa = _lade(pfade.PRICEACTION_JSON)
    pivot_backtest = _lade(PIVOT_BACKTEST)
    if not signals or not signals.get("treffer"):
        print("Top-Setups: keine signals.json - uebersprungen.")
        return False

    kauf = (signals.get("schwellen") or {}).get("kauf_kandidat", 70)
    regime = signals.get("marktregime") or {}
    pivmap = {e.get("ticker"): e for e in (pivot or {}).get("treffer", [])}
    pamap = {e.get("ticker"): e.get("pa_score")
             for e in (pa or {}).get("treffer", [])}

    setups = []
    for e in signals["treffer"]:
        if e.get("score", 0) < kauf:
            continue
        p = pivmap.get(e.get("ticker"))
        if not p or p.get("pivot_status") not in ("ARMED", "BREAKOUT"):
            continue
        pa_score = pamap.get(e.get("ticker"))
        if pa_score is None or pa_score <= 0:
            continue
        earn = e.get("earnings") or {}
        bt = _backtest_info(pivot_backtest, p.get("pivot_status"))
        setups.append({
            "ticker": e.get("ticker"),
            "name": e.get("name"),
            "markt": e.get("markt"),
            "score": e.get("score"),
            "pa_score": pa_score,
            "pivot_status": p.get("pivot_status"),
            "qualitaet": p.get("qualitaet"),
            "pivot": p.get("pivot"),
            "stop": p.get("stop"),
            # naechster Earnings-Termin in Tagen (Frontend wendet warnTage() an)
            "earnings_tage": earn.get("tage") if earn.get("status") == "termin" else None,
            "regime": (regime.get(e.get("markt")) or {}).get("ampel"),
            # Depot-Abgleich (seit 2026-08-20): scorer.py berechnet im_depot
            # bereits gegen mts_data.json (Namensabgleich) - hier nur
            # durchreichen, damit das Startseiten-Panel Doppelkaeufe zeigt,
            # ohne erst auf setup-detail.html klicken zu muessen.
            "im_depot": bool(e.get("im_depot")),
            # Konfluenz & Kern-Setups (siehe Modul-Docstring):
            "quellen_unabhaengig": len((e.get("quellen") or {}).get("unabhaengig") or []),
            "backtest": bt,
            "kern_setup": bool(bt and bt["win"] >= KERN_WIN_SCHWELLE),
        })

    # Beste zuerst: Kern-Setup (Backtest-bestaetigte Kohorte, seit 2026-08-17)
    # vor ARMED vor BREAKOUT, dann Pivot-Qualitaet. Bis 2026-08-02 war BREAKOUT
    # vorn - der frische, unverzerrte Forward-Test (Signal-Hub/src/
    # pivot_backtest.py --evaluate) zeigt aber ARMED bei 71% Win-Rate (n=83)
    # gegen nur 34% bei BREAKOUT (n=90); der Retro-Backtest hatte BREAKOUT
    # wegen Universums-Bias faelschlich gut aussehen lassen (siehe Bias-Hinweis
    # in pivot_backtest.py). Bei genug neuen Forward-Daten erneut pruefen.
    rang = {"ARMED": 1, "BREAKOUT": 0}
    setups.sort(key=lambda s: (s["kern_setup"], rang.get(s["pivot_status"], 0), s["qualitaet"] or 0),
                reverse=True)
    setups = setups[:MAX_SETUPS]

    aktiv = sum(1 for s in setups if s["regime"] == "gruen")
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "schwelle": kauf,
        "marktregime": regime,
        "anzahl": len(setups),
        "setups": setups,
    }
    os.makedirs(_SH_DATA, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.TOP_SETUPS = ")
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")
    print(f"Top-Setups: {len(setups)} Setups ({aktiv} in gruenem Regime) -> {OUT_JSON}")

    # Push NEUER Setups - scheitert nie hart (wie der Rest dieser Datei: ein
    # kaputter/fehlender ntfy-Kanal darf das Aggregat selbst nie verhindern).
    try:
        state = _state_load()
        neu = _neuzugaenge(setups, state)
        _state_save(state)
        _push(neu)
    except Exception as ex:
        print(f"Top-Setups-Push uebersprungen ({ex}).")

    return True


if __name__ == "__main__":
    schreibe()
