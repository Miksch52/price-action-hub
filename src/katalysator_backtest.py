#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forward-Test des Katalysator-Layers (katalysator.py).

Misst, OB ein Top-Setup mit erkanntem fundamentalen Katalysator (Zahlen,
Guidance, Auftrag, Zulassung, Analyst, Index, Uebernahme) tatsaechlich einen
besseren Forward-Return liefert als eines ohne - unverzerrt, weil die
Einordnung VOR dem Ergebnis feststand. Gleiches Grundprinzip wie
hebel_backtest.py/muster_backtest.py (--log/--evaluate, kein Retro-Modus,
Kohorten nach Kalendertagen gereift).

Nur Ticker, fuer die katalysator.py an diesem Tag tatsaechlich eine Antwort
bekommen hat, werden geloggt - "mit_katalysator" (klasse != "keiner") vs.
"ohne_katalysator" (klasse == "keiner", also eine ECHTE "kein Anlass
gefunden"-Antwort). Ticker, die katalysator.py an diesem Tag gar nicht
abgefragt hat (Kostendeckel MAX_TICKER, fehlender API-Key, Netzwerkfehler),
werden bewusst in KEINE der beiden Kohorten gezaehlt - sonst wuerde ein
unbekannter Zustand faelschlich als "kein Katalysator" gewertet, analog zur
gleichen Unterscheidung im Signal-Fingerabdruck der Hauptapp.

  python3 src/katalysator_backtest.py --log       # haengt die heutigen
        katalysator.py-Ergebnisse mit Datum+Kurs ans Forward-Logbuch.
  python3 src/katalysator_backtest.py --evaluate  # bewertet gereifte Picks
        (>=21/50/78 Kalendertage) gegen aktuelle Yahoo-Kurse.

run.py ruft log_und_evaluate() nach katalysator.taeglich_falls_faellig() auf.

Ausgabe: Signal-Hub/data/katalysator_backtest.json (+ .js).
"""

import json
import os
import sys
from datetime import datetime, timezone

import pfade
import kursdaten

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
STUFEN = ("mit_katalysator", "ohne_katalysator")


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
    if elapsed_tage >= 78:
        return "12W"
    if elapsed_tage >= 50:
        return "8W"
    if elapsed_tage >= 21:
        return "4W"
    return None


# ---------------------------------------------------------------------------
def _logbuch_load():
    if os.path.exists(pfade.KATALYSATOR_LOGBUCH):
        try:
            return json.load(open(pfade.KATALYSATOR_LOGBUCH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _logbuch_save(lb):
    with open(pfade.KATALYSATOR_LOGBUCH, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)


def log_heute():
    if not os.path.exists(pfade.KATALYSATOR_JSON):
        print("Keine top_setups_katalysator.json -> nichts zu loggen.")
        return
    if not os.path.exists(pfade.SIGNAL_HUB_TOP_SETUPS_JSON):
        print("Keine top_setups.json (Kurse) -> nichts zu loggen.")
        return
    kat = json.load(open(pfade.KATALYSATOR_JSON, encoding="utf-8")).get("katalysatoren") or {}
    if not kat:
        print("Katalysator-Ergebnis heute leer -> nichts zu loggen.")
        return
    preise = {s.get("ticker"): s for s in
              (json.load(open(pfade.SIGNAL_HUB_TOP_SETUPS_JSON, encoding="utf-8")).get("setups") or [])}

    heute = datetime.now().strftime("%Y-%m-%d")
    lb = _logbuch_load()
    bekannt = {(e["datum"], e["ticker"]) for e in lb}
    neu = 0
    for ticker, k in kat.items():
        s = preise.get(ticker)
        if not s or not s.get("preis"):
            continue
        key = (heute, ticker)
        if key in bekannt:
            continue
        stufe = "mit_katalysator" if k.get("klasse") not in (None, "keiner") else "ohne_katalysator"
        lb.append({
            "datum": heute, "ticker": ticker, "markt": s.get("markt"),
            "stufe": stufe, "klasse": k.get("klasse"), "preis_signal": s.get("preis"),
        })
        neu += 1
    lb = lb[-5000:]
    _logbuch_save(lb)
    print(f"Katalysator-Forward-Logbuch: {neu} neue Picks ergaenzt (gesamt {len(lb)}).")


# ---------------------------------------------------------------------------
def evaluate():
    lb = _logbuch_load()
    if not lb:
        print("Katalysator-Logbuch leer -> erst --log sammeln lassen.")
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
        sym = e.get("ticker")
        if sym not in aktuell:
            d = kursdaten.hole_chart_cached(sym, cache, heute_str)
            aktuell[sym] = (d.get("closes")[-1] if d and d.get("closes") else None)
        kurs = aktuell[sym]
        if not kurs or not e.get("preis_signal"):
            continue
        ret = kurs / e["preis_signal"] - 1
        eimer[e["stufe"]][bk].append(ret)
        einzelfaelle.append({
            "ticker": e["ticker"], "stufe": e["stufe"], "klasse": e.get("klasse"),
            "datum": e["datum"], "preis_signal": e["preis_signal"],
            "horizont": bk, "return_pct": round(ret * 100, 2),
        })
    kursdaten.speichere_cache(cache)
    fr = {s: {h: _stats(eimer[s][h]) for h, _ in HORIZONTE} for s in eimer}
    return fr, einzelfaelle


# ---------------------------------------------------------------------------
def _schreibe(out):
    with open(pfade.KATALYSATOR_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(pfade.KATALYSATOR_BACKTEST_JS, "w", encoding="utf-8") as f:
        f.write("window.KATALYSATOR_BACKTEST_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")


def _druck_tabelle(fr):
    print(f"{'Stufe':17s}{'Hor':5s}{'n':>5s}{'Win%':>7s}{'Ø%':>8s}")
    for stufe in STUFEN:
        for label, _ in HORIZONTE:
            s = fr.get(stufe, {}).get(label) or {}
            if not s.get("n"):
                continue
            print(f"{stufe:17s}{label:5s}{s['n']:5d}{s['win']:7.1f}{s['avg']:8.2f}")


def log_und_evaluate():
    log_heute()
    fr, einzelfaelle = evaluate()
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hinweis": ("Forward-Test: Kurs am Signaltag (katalysator.py-Einordnung mit/ohne "
                    "erkannten Katalysator) vs. aktueller Kurs, Kohorten nach Alter "
                    "(>=21/50/78 Kalendertage). Unverzerrt (Einordnung stand vor dem Ergebnis "
                    "fest). Nur Ticker mit tatsaechlicher Katalysator-Antwort des jeweiligen "
                    "Tages gezaehlt - nicht abgefragte Ticker fliessen in KEINE Kohorte ein."),
        "forward_realisiert": fr,
        "forward_einzelfaelle": einzelfaelle,
    }
    _schreibe(out)
    if einzelfaelle:
        print(f"\n=== Katalysator-Layer Forward-Test ({len(einzelfaelle)} gereifte Einzelfaelle) ===")
        _druck_tabelle(fr)
    print(f"Gespeichert: {pfade.KATALYSATOR_BACKTEST}")
    return fr, einzelfaelle


def main():
    args = sys.argv[1:]
    if "--log" in args:
        log_heute()
        return
    if "--evaluate" in args:
        log_und_evaluate()
        return
    print("Nutzung: katalysator_backtest.py --log | --evaluate")


if __name__ == "__main__":
    main()
