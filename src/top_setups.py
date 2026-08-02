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
  Signal-Hub/data/signals.json          - score, tier, markt, marktregime, earnings
  Signal-Hub/data/pivot.json            - pivot_status (ARMED/BREAKOUT), qualitaet, pivot, stop
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

Ausgabe: Signal-Hub/data/top_setups.json (+ .js fuer file:///Pages).
"""

import json
import os
from datetime import datetime, timezone

import pfade

_SH_DATA = os.path.join(pfade.REPO_ROOT, "Signal-Hub", "data")
SIGNALS = pfade.SIGNAL_HUB_SIGNALS_JSON
PIVOT = os.path.join(_SH_DATA, "pivot.json")
OUT_JSON = os.path.join(_SH_DATA, "top_setups.json")
OUT_JS = os.path.join(_SH_DATA, "top_setups.js")

MAX_SETUPS = 24     # winzig halten - das Panel zeigt ohnehin nur die Spitze


def _lade(pfad):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def schreibe():
    signals = _lade(SIGNALS)
    pivot = _lade(PIVOT)
    pa = _lade(pfade.PRICEACTION_JSON)
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
        })

    # Beste zuerst: ARMED vor BREAKOUT, dann Pivot-Qualitaet. Bis 2026-08-02 war
    # BREAKOUT vorn - der frische, unverzerrte Forward-Test (Signal-Hub/src/
    # pivot_backtest.py --evaluate) zeigt aber ARMED bei 71% Win-Rate (n=83)
    # gegen nur 34% bei BREAKOUT (n=90); der Retro-Backtest hatte BREAKOUT
    # wegen Universums-Bias faelschlich gut aussehen lassen (siehe Bias-Hinweis
    # in pivot_backtest.py). Bei genug neuen Forward-Daten erneut pruefen.
    rang = {"ARMED": 1, "BREAKOUT": 0}
    setups.sort(key=lambda s: (rang.get(s["pivot_status"], 0), s["qualitaet"] or 0),
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
    return True


if __name__ == "__main__":
    schreibe()
