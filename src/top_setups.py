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
  - kern_setup: True nur, wenn der Forward-Backtest fuer GENAU DIESEN
    Pivot-Status (ARMED/BREAKOUT) einen STATISTISCH BELEGTEN Vorteil zeigt -
    groesster Datentopf, mindestens KERN_REIFE_N gereifte Picks, Win-Rate
    >= KERN_WIN_SCHWELLE und Untergrenze des 95%-Konfidenzintervalls ueber
    KERN_CI_UNTERGRENZE (siehe Kommentar bei den Konstanten: die alte Fassung
    vergab den Stern ab n=8 und hat damit Rauschen ausgezeichnet). Das Feld
    "backtest" wird davon unabhaengig IMMER mitgeliefert, sobald ueberhaupt
    Beobachtungen vorliegen - inklusive "reif"-Flag und Konfidenzintervall,
    damit das Frontend die gemessene Zahl zeigen kann statt einer
    Auszeichnung ohne Beleg. Rotation-Dashboard
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
import math
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

# Kern-Setup-Schwellen (seit 2026-08-17, verschaerft 2026-08-23 nach der
# Systempruefung - siehe Modul-Docstring "Konfluenz & Kern-Setups").
#
# WARUM DIE ALTE FASSUNG FALSCH WAR (bis 2026-08-23): KERN_REIFE_N stand auf 8
# und BACKTEST_HORIZONTE waehlte den LAENGSTEN Horizont zuerst ("reifer =
# aussagekraeftiger"). Beides zusammen ergab systematisch den KLEINSTEN,
# verrauschtesten Datentopf: fuer ARMED wurde am 2026-08-23 der 8W-Wert mit
# n=12 genommen, waehrend derselbe Backtest bei 4W n=1005 auswies. Bei n=8
# reicht das 95%-Konfidenzintervall einer beobachteten Win-Rate von 60% grob
# von 26% bis 88% - der Stern behauptete Wissen, wo statistisch ein Muenzwurf
# stand. Genau so kam auch die Rangfolge-Entscheidung "ARMED bei 71% (n=83)"
# zustande; bei n=1005 lag derselbe Wert spaeter bei 48,8%.
#
# NEUE REGEL - drei Bedingungen, alle drei muessen halten:
#   1. groesster verfuegbarer Datentopf (nicht laengster Horizont)
#   2. mindestens KERN_REIFE_N gereifte Picks
#   3. Win-Rate >= KERN_WIN_SCHWELLE UND die UNTERGRENZE des 95%-Wilson-
#      Konfidenzintervalls > KERN_CI_UNTERGRENZE - erst dann ist "besser als
#      Muenzwurf" statistisch belegt und nicht nur beobachtet.
KERN_REIFE_N = 100
KERN_WIN_SCHWELLE = 60.0
KERN_CI_UNTERGRENZE = 50.0
BACKTEST_HORIZONTE = ("4W", "8W", "12W")  # Reihenfolge nur noch Tie-Break bei gleichem n

AMPEL_ICON = {"gruen": "🟢", "gelb": "🟡", "rot": "🔴"}
FLAGGE = {"USA": "US", "Europa": "EU"}

# Trend-Template-Kernfaktoren fuer die Kauf-Review-Queue (seit 2026-08-20,
# Top-Setups-Roadmap Punkt 7): bewusst nur die Ampel (gruen/gelb/rot), nicht
# der volle Detailtext - haelt top_setups.json winzig (siehe Moduldocstring
# "statt der 11.8 MB signals.js"). Reihenfolge = Anzeigereihenfolge im
# Frontend-Checklisten-Widget.
TREND_TEMPLATE_FAKTOREN = [
    "stage2_trend", "relative_staerke", "naehe_52w_hoch",
    "basis_konsolidierung", "volumen_bestaetigung",
]


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
    # Titel bewusst ohne "🏆 Top": die Schnittmenge ist eine Vorauswahl, kein
    # belegtes Guetesiegel (siehe _ist_kern) - gleiche Sprache wie das Panel
    # auf der Startseite, damit Push und Oberflaeche nicht auseinanderlaufen.
    titel = f"🧩 {len(neu)} neue{'s' if len(neu) == 1 else ''} Setup{'' if len(neu) == 1 else 's'}"
    ok = _sende_ntfy(titel, "\n".join(zeilen))
    print(f"Top-Setups-Push {'gesendet' if ok else 'fehlgeschlagen (kein Thema oder ntfy-Fehler)'}: {titel}")


def _wilson(win_pct, n, z=1.96):
    """95%-Konfidenzintervall einer Trefferquote nach Wilson (nicht die
    Normalapproximation - die bricht bei kleinen n und Quoten nahe 0/100
    zusammen, genau dort, wo dieser Backtest bisher seine Sterne vergeben hat).

    Rueckgabe: (untergrenze_pct, obergrenze_pct), beide auf eine Nachkommastelle.
    """
    if not n:
        return None, None
    p = win_pct / 100.0
    nenner = 1 + z * z / n
    mitte = (p + z * z / (2 * n)) / nenner
    spanne = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / nenner
    return (round(max(0.0, mitte - spanne) * 100, 1),
            round(min(1.0, mitte + spanne) * 100, 1))


def _backtest_info(pivot_backtest, status):
    """Belastbarste Forward-Test-Kohorte fuer einen Pivot-Status liefern.

    Auswahl ueber die GROESSTE Stichprobe, nicht den laengsten Horizont (siehe
    Kommentar bei KERN_REIFE_N oben). Liefert bewusst auch UNREIFE Kohorten
    zurueck (Feld "reif") statt None - das Frontend soll die tatsaechlich
    gemessene Zahl anzeigen koennen ("48,8 % bei n=1005") statt gar nichts;
    ausgezeichnet (kern_setup) wird davon nur, was alle drei Bedingungen
    erfuellt. None nur, wenn ueberhaupt keine Beobachtung vorliegt.
    """
    block = ((pivot_backtest or {}).get("forward_realisiert") or {}).get(status) or {}
    kandidaten = [
        (block[label]["n"], -BACKTEST_HORIZONTE.index(label), label, block[label])
        for label in BACKTEST_HORIZONTE
        if block.get(label) and (block[label].get("n") or 0) > 0
        and block[label].get("win") is not None
    ]
    if not kandidaten:
        return None
    _, _, label, s = max(kandidaten)
    ci_low, ci_high = _wilson(s["win"], s["n"])
    return {
        "win": s["win"], "n": s["n"], "horizont": label,
        "ci_low": ci_low, "ci_high": ci_high,
        "reif": s["n"] >= KERN_REIFE_N,
    }


def _ist_kern(bt):
    """Kern-Setup nur bei belegter Ueberlegenheit - siehe KERN_REIFE_N oben."""
    return bool(
        bt and bt["reif"]
        and bt["win"] >= KERN_WIN_SCHWELLE
        and (bt["ci_low"] or 0) > KERN_CI_UNTERGRENZE
    )


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
        faktoren = e.get("faktoren") or {}
        setups.append({
            "ticker": e.get("ticker"),
            "name": e.get("name"),
            "markt": e.get("markt"),
            "preis": e.get("preis"),
            "score": e.get("score"),
            # Trend-Template-Ampeln fuer die Kauf-Review-Queue (Punkt 7):
            # nur die Ampel je Kernfaktor, siehe TREND_TEMPLATE_FAKTOREN oben.
            "trend_template": {
                k: (faktoren.get(k) or {}).get("ampel") for k in TREND_TEMPLATE_FAKTOREN
            },
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
            "kern_setup": _ist_kern(bt),
        })

    # Beste zuerst: Kern-Setup (belegte Kohorte, siehe _ist_kern) vor ARMED vor
    # BREAKOUT, dann Pivot-Qualitaet.
    #
    # Stand 2026-08-23 (4W, groesster Datentopf): ARMED 48,8 % bei n=1005,
    # BREAKOUT 35,2 % bei n=108. ARMED bleibt also vorn - aber nicht mehr wegen
    # der urspruenglichen 71 % (n=83, Stand 2026-08-17), die sich bei
    # zwoelffacher Stichprobe als Rauschen erwiesen haben, sondern nur noch als
    # relative Reihung zweier Kohorten, von denen KEINE einen belegten Vorteil
    # zeigt. Beide liegen im Bereich Muenzwurf bzw. darunter; die absolute
    # Aussage "das ist ein gutes Setup" traegt derzeit keine der beiden.
    # Erneut pruefen, sobald eine Kohorte die _ist_kern-Bedingungen erfuellt.
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
