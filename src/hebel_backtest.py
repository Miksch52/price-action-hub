#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forward-Test fuer die Hebel-Ampel (scorer.py::hebel_ampel()).

Misst, OB "gruen" (alle Kriterien erfuellt) tatsaechlich einen besseren
Forward-Return liefert als "gelb" (Markt/Trend intakt, aber noch nicht
reif) - unverzerrt, weil die Einstufung VOR dem Ergebnis feststand.
Gleiches Grundprinzip wie Signal-Hub/src/pivot_backtest.py (--log/
--evaluate), hier bewusst OHNE Retro-Modus: die Hebel-Ampel kombiniert
mehrere bereits fertige Fremd-Ampeln (Markt-Regime, Pivot-Status, Stage-2-
Trend, ...) - eine historische Walk-Forward-Rekonstruktion muesste all das
rueckwirkend neu bestimmen. Das lohnt sich fuer einen ersten Baustein
nicht; die Forward-Log-Stichprobe reift stattdessen einfach ueber
Kalenderzeit, wie es das Pivot-Vorbild vorgemacht hat.

  python3 src/hebel_backtest.py --log       # haengt heutige "gruen"/"gelb"-
        Ticker aus data/priceaction.json mit Datum+Kurs ans Forward-Logbuch
        (~/Library/Application Support/PriceActionHub/hebel_logbuch.json).
  python3 src/hebel_backtest.py --evaluate  # bewertet gereifte Picks
        (>=21/50/78 Kalendertage) gegen aktuelle Yahoo-Kurse, schreibt
        data/hebel_backtest.json.

run.py ruft log_und_evaluate() bei jedem Lauf auf (kein eigenes
Scheduling wie bei Signal-Hubs woechentlichem Pivot-Evaluations-Loop -
die Hebel-Stichprobe ist klein genug, um jedes Mal mitzulaufen).
"""

import json
import os
import sys
from datetime import datetime, timezone

import pfade
import kursdaten

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
STUFEN = ("gruen", "gelb")


def _stats(rets):
    if not rets:
        return {"n": 0, "win": None, "avg": None, "median": None}
    rets = sorted(rets)
    n = len(rets)
    win = sum(1 for r in rets if r > 0) / n
    avg = sum(rets) / n
    median = rets[n // 2] if n % 2 else (rets[n // 2 - 1] + rets[n // 2]) / 2
    return {"n": n, "win": round(win * 100, 1),
            "avg": round(avg * 100, 2), "median": round(median * 100, 2)}


def _bucket(elapsed_tage):
    """Kalendertage -> reifster Horizont (oder None, wenn noch zu jung)."""
    if elapsed_tage >= 78:
        return "12W"
    if elapsed_tage >= 50:
        return "8W"
    if elapsed_tage >= 21:
        return "4W"
    return None


# ---------------------------------------------------------------------------
def _logbuch_load():
    if os.path.exists(pfade.HEBEL_LOGBUCH):
        try:
            return json.load(open(pfade.HEBEL_LOGBUCH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _logbuch_save(lb):
    with open(pfade.HEBEL_LOGBUCH, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)


def log_heute():
    if not os.path.exists(pfade.PRICEACTION_JSON):
        print("Keine priceaction.json -> nichts zu loggen.")
        return
    daten = json.load(open(pfade.PRICEACTION_JSON, encoding="utf-8"))
    heute = datetime.now().strftime("%Y-%m-%d")
    lb = _logbuch_load()
    bekannt = {(e["datum"], e["ticker"], e["stufe"]) for e in lb}
    neu = 0
    for t in daten.get("treffer", []):
        stufe = (t.get("hebel_ampel") or {}).get("stufe")
        if stufe not in STUFEN:
            continue
        key = (heute, t.get("ticker"), stufe)
        if key in bekannt:
            continue
        lb.append({
            "datum": heute, "ticker": t.get("ticker"),
            "yahoo_symbol": t.get("yahoo_symbol"), "markt": t.get("markt"),
            "stufe": stufe, "preis_signal": t.get("preis"),
        })
        neu += 1
    lb = lb[-5000:]
    _logbuch_save(lb)
    print(f"Hebel-Forward-Logbuch: {neu} neue Picks ergaenzt (gesamt {len(lb)}).")


# ---------------------------------------------------------------------------
def evaluate():
    lb = _logbuch_load()
    if not lb:
        print("Hebel-Logbuch leer -> erst --log sammeln lassen.")
        return {}, []
    cache = kursdaten.lade_cache()
    heute_dt = datetime.now().date()
    heute_str = heute_dt.isoformat()
    eimer = {s: {h: [] for h, _ in HORIZONTE} for s in STUFEN}
    einzelfaelle = []
    aktuell = {}
    for e in lb:
        try:
            tage = (heute_dt - datetime.strptime(e["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        bk = _bucket(tage)
        if not bk or e.get("stufe") not in eimer:
            continue
        sym = e.get("yahoo_symbol") or e.get("ticker")
        if sym not in aktuell:
            d = kursdaten.hole_chart_cached(sym, cache, heute_str)
            aktuell[sym] = (d.get("closes")[-1] if d and d.get("closes") else None)
        kurs = aktuell[sym]
        if not kurs or not e.get("preis_signal"):
            continue
        ret = kurs / e["preis_signal"] - 1
        eimer[e["stufe"]][bk].append(ret)
        einzelfaelle.append({
            "ticker": e["ticker"], "yahoo_symbol": sym, "stufe": e["stufe"],
            "datum": e["datum"], "preis_signal": e["preis_signal"],
            "horizont": bk, "return_pct": round(ret * 100, 2),
        })
    kursdaten.speichere_cache(cache)
    fr = {s: {h: _stats(eimer[s][h]) for h, _ in HORIZONTE} for s in eimer}
    return fr, einzelfaelle


# ---------------------------------------------------------------------------
def _schreibe(out):
    with open(pfade.HEBEL_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(pfade.HEBEL_BACKTEST_JS, "w", encoding="utf-8") as f:
        f.write("window.HEBEL_BACKTEST_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")


def _druck_tabelle(fr):
    print(f"{'Stufe':7s}{'Hor':5s}{'n':>5s}{'Win%':>7s}{'Ø%':>8s}")
    for stufe in STUFEN:
        for label, _ in HORIZONTE:
            s = fr.get(stufe, {}).get(label) or {}
            if not s.get("n"):
                continue
            print(f"{stufe:7s}{label:5s}{s['n']:5d}{s['win']:7.1f}{s['avg']:8.2f}")


def log_und_evaluate():
    """Bequemer Einstiegspunkt fuer run.py: loggen + auswerten + schreiben in
    einem Aufruf."""
    log_heute()
    fr, einzelfaelle = evaluate()
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hinweis": ("Forward-Test: Kurs am Signaltag (hebel_ampel.stufe gruen/gelb) "
                    "vs. aktueller Kurs, Kohorten nach Alter (>=21/50/78 Kalendertage). "
                    "Unverzerrt (Einstufung stand vor dem Ergebnis fest). Kein Retro-Modus "
                    "(siehe Docstring in hebel_backtest.py) - die Stichprobe wird erst "
                    "ueber Kalenderzeit aussagekraeftig."),
        "forward_realisiert": fr,
        "forward_einzelfaelle": einzelfaelle,
    }
    _schreibe(out)
    if einzelfaelle:
        print(f"\n=== Hebel-Ampel Forward-Test ({len(einzelfaelle)} gereifte Einzelfaelle) ===")
        _druck_tabelle(fr)
    print(f"Gespeichert: {pfade.HEBEL_BACKTEST}")
    return fr, einzelfaelle


def main():
    args = sys.argv[1:]
    if "--log" in args:
        log_heute()
        return
    if "--evaluate" in args:
        log_und_evaluate()
        return
    print("Nutzung: hebel_backtest.py --log | --evaluate")


if __name__ == "__main__":
    main()
