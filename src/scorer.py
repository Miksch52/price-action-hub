#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scorer fuer den Price-Action-Hub.

Liest die Ticker-Universe des Signal-Hub NUR aus dessen Ausgabedatei
(Symbol/Name/Markt, KEINE Scores/Faktoren -- Entflechtung, siehe
pfade.py-Docstring), holt dafuer eigenstaendig echtes OHLC ueber
kursdaten.py und wendet muster.analysiere() an. Der resultierende
Price-Action-Score dient nur der Dashboard-Sortierung, ist kein Ersatz
fuer den Signal-Hub-Momentum-Score.

Ausgabe: data/priceaction.json (+ data/priceaction.js fuers Dashboard
ueber file://).

Test: python3 scorer.py
"""

import json
import os
import sys
from datetime import date, datetime, timezone

import pfade
import kursdaten
import muster

CHART_FENSTER = 126  # ~6 Monate, konsistent mit Signal-Hub-Minichart-Fenster


def lade_tickerliste():
    """Nur Symbol/Name/Markt aus signals.json -- keine Scores uebernehmen."""
    if not os.path.exists(pfade.SIGNAL_HUB_SIGNALS_JSON):
        print(f"Keine Signal-Hub-Daten gefunden ({pfade.SIGNAL_HUB_SIGNALS_JSON}) - nichts zu tun.")
        return [], None
    with open(pfade.SIGNAL_HUB_SIGNALS_JSON, encoding="utf-8") as f:
        d = json.load(f)
    ticker = []
    gesehen = set()
    for t in d.get("treffer", []):
        symbol = t.get("yahoo_symbol") or t.get("ticker")
        if not symbol or symbol in gesehen:
            continue
        gesehen.add(symbol)
        ticker.append({
            "ticker": t.get("ticker"),
            "yahoo_symbol": symbol,
            "name": t.get("name"),
            "markt": t.get("markt"),
            "exchange": t.get("exchange"),
        })
    return ticker, d.get("erstellt")


def _bull_kontext(stadium):
    return stadium in ("Advancing", "Accumulation")


def _bear_kontext(stadium):
    return stadium in ("Distribution", "Declining")


def _gewichtet(wert, ist_bull_signal, stadium):
    """Signale in Trendrichtung des Marktstadiums voll werten, Signale
    gegen den Trend gedaempft (Gegentrend-Setups sind riskanter)."""
    if stadium is None:
        return wert
    aligned = (ist_bull_signal and _bull_kontext(stadium)) or (not ist_bull_signal and _bear_kontext(stadium))
    return wert if aligned else wert * 0.5


def pa_score(m):
    """Einfache, transparente Summe aus den Musterergebnissen. Positiv =
    bullisches Chartbild, negativ = baerisch. Nur Sortierhilfe, kein
    Ersatz fuer den Signal-Hub-Score."""
    if m.get("status") != "ok":
        return 0.0
    stadium = m["marktstadium"].get("stadium")
    score = 0.0

    tb = m["trend_bar"]
    if tb.get("stark"):
        score += _gewichtet(2.0 if tb["richtung"] == "bull" else -2.0, tb["richtung"] == "bull", stadium)

    bc = m["bar_counting"]
    if bc.get("zuverlaessig") and bc.get("typ"):
        ist_bull = bc["typ"].startswith("High")
        score += _gewichtet(1.0 if ist_bull else -1.0, ist_bull, stadium)

    bo = m["breakout"]
    if bo.get("breakout_up"):
        score += _gewichtet(-1.0 if bo.get("failed") else 2.0, not bo.get("failed"), stadium)
    elif bo.get("breakout_down"):
        score += _gewichtet(1.0 if bo.get("failed") else -2.0, bool(bo.get("failed")), stadium)

    gp = m["gap"]
    if gp.get("gap_up") and not gp.get("gefuellt"):
        score += _gewichtet(1.0, True, stadium)
    elif gp.get("gap_down") and not gp.get("gefuellt"):
        score += _gewichtet(-1.0, False, stadium)

    return round(score, 2)


def _chartdaten(ohlc, fenster=CHART_FENSTER):
    fenster = min(fenster, len(ohlc["closes"]))
    return {
        "o": [round(x, 2) for x in ohlc["opens"][-fenster:]],
        "h": [round(x, 2) for x in ohlc["highs"][-fenster:]],
        "l": [round(x, 2) for x in ohlc["lows"][-fenster:]],
        "c": [round(x, 2) for x in ohlc["closes"][-fenster:]],
        "v": [int(x / 1000) for x in ohlc["volumes"][-fenster:]],
    }


def score_alle():
    ticker, basis_erstellt = lade_tickerliste()
    if not ticker:
        return False

    cache = kursdaten.lade_cache()
    heute = date.today().isoformat()
    kursdaten.prefetch_charts_parallel([t["yahoo_symbol"] for t in ticker], cache, heute)
    treffer = []
    print(f"Price-Action-Hub: analysiere {len(ticker)} Ticker ...")
    for i, t in enumerate(ticker, 1):
        symbol = t["yahoo_symbol"]
        ohlc = kursdaten.hole_chart_cached(symbol, cache, heute)
        if not ohlc:
            continue
        m = muster.analysiere(ohlc)
        if m.get("status") != "ok":
            continue
        treffer.append({
            **{k: v for k, v in t.items()},
            "preis": round(ohlc["closes"][-1], 2),
            "pa_score": pa_score(m),
            "muster": m,
            "chart": _chartdaten(ohlc),
        })
        if i % 25 == 0:
            print(f"  {i}/{len(ticker)} verarbeitet ...")
    kursdaten.speichere_cache(cache)

    treffer.sort(key=lambda t: t["pa_score"], reverse=True)
    ausgabe = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "basis_signals": basis_erstellt,
        "anzahl": len(treffer),
        "treffer": treffer,
    }

    with open(pfade.PRICEACTION_JSON, "w", encoding="utf-8") as f:
        json.dump(ausgabe, f, ensure_ascii=False, separators=(",", ":"))
    with open(pfade.PRICEACTION_JS, "w", encoding="utf-8") as f:
        f.write("window.PRICEACTION_DATA = ")
        json.dump(ausgabe, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")

    bullisch = sum(1 for t in treffer if t["pa_score"] > 0)
    baerisch = sum(1 for t in treffer if t["pa_score"] < 0)
    print(f"Fertig: {len(treffer)} Ticker analysiert ({bullisch} bullisch, {baerisch} baerisch).")
    return True


if __name__ == "__main__":
    ok = score_alle()
    sys.exit(0 if ok else 1)
