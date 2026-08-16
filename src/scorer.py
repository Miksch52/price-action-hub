#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scorer fuer den Price-Action-Hub.

Liest die Ticker-Universe des Signal-Hub aus dessen Ausgabedatei (Symbol/
Name/Markt -- Entflechtung, siehe pfade.py-Docstring), holt dafuer
eigenstaendig echtes OHLC ueber kursdaten.py und wendet muster.analysiere()
an. Der resultierende Price-Action-Score dient nur der Dashboard-Sortierung,
ist kein Ersatz fuer den Signal-Hub-Momentum-Score.

Seit 2026-08-16 zusaetzlich: ein eng begrenztes Set an fertigen Signal-Hub-
Ampelfeldern je Ticker (Stage-2-Trend, Basis/VCP, Extended, Earnings, 50/80,
Klimax, Marktregime) + der Pivot-Status aus SIGNAL_HUB_PIVOT_JSON werden
durchgereicht und zur Hebel-Ampel (hebel_ampel()) kombiniert - reines
Zusammenfuehren bereits fertiger Werte, keine Neuberechnung (siehe
pfade.py-Docstring fuer die Begruendung dieser gezielten Ausnahme).

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
    """Symbol/Name/Markt aus signals.json, PLUS (seit 2026-08-16, siehe
    pfade.py-Docstring) ein gezielt eng begrenztes Set an bereits fertigen
    Hebel-Ampel-Feldern je Ticker -- reines Durchreichen, keine Neuberechnung.
    """
    if not os.path.exists(pfade.SIGNAL_HUB_SIGNALS_JSON):
        print(f"Keine Signal-Hub-Daten gefunden ({pfade.SIGNAL_HUB_SIGNALS_JSON}) - nichts zu tun.")
        return [], None, {}
    with open(pfade.SIGNAL_HUB_SIGNALS_JSON, encoding="utf-8") as f:
        d = json.load(f)
    ticker = []
    gesehen = set()
    for t in d.get("treffer", []):
        symbol = t.get("yahoo_symbol") or t.get("ticker")
        if not symbol or symbol in gesehen:
            continue
        gesehen.add(symbol)
        fak = t.get("faktoren") or {}
        ticker.append({
            "ticker": t.get("ticker"),
            "yahoo_symbol": symbol,
            "name": t.get("name"),
            "markt": t.get("markt"),
            "exchange": t.get("exchange"),
            "_hebel_kontext": {
                "stage2_ampel": (fak.get("stage2_trend") or {}).get("ampel"),
                "basis_ampel": (fak.get("basis_konsolidierung") or {}).get("ampel"),
                "extended_pct": t.get("extended_pct"),
                "earnings": t.get("earnings"),
                "minervini_5080": t.get("minervini_5080"),
                "klimax_warnung": t.get("klimax_warnung"),
            },
        })
    return ticker, d.get("erstellt"), (d.get("marktregime") or {})


def lade_pivotmap():
    """Pivot-Status (ARMED/BREAKOUT) je Ticker - gleiche Quelle wie
    top_setups.py, hier zusaetzlich fuer die Hebel-Ampel gebraucht."""
    if not os.path.exists(pfade.SIGNAL_HUB_PIVOT_JSON):
        return {}
    try:
        with open(pfade.SIGNAL_HUB_PIVOT_JSON, encoding="utf-8") as f:
            d = json.load(f)
        return {e.get("ticker"): e.get("pivot_status") for e in d.get("treffer", [])}
    except Exception:
        return {}


def lade_warn_tage():
    """earnings.warn_tage aus Signal-Hub/config.json (reiner Datei-Read,
    gleiche Quelle, die signal-hub.html clientseitig ueber CONFIG liest) -
    damit die Earnings-Schwelle in beiden Apps aus demselben Wert kommt,
    statt in Python hart kodiert zu sein und irgendwann auseinanderzulaufen."""
    try:
        with open(pfade.SIGNAL_HUB_CONFIG, encoding="utf-8") as f:
            return (json.load(f).get("earnings") or {}).get("warn_tage") or 10
    except Exception:
        return 10


def hebel_ampel(kontext, markt, regime, pivot_status, warn_tage):
    """Hebel-Trade-Reife-Ampel (2026-08-16): identische Kriterien wie
    Signal-Hub/signal-hub.html::hebelAmpel() - bei Aenderungen HIER immer
    auch DORT nachziehen (kein Cross-App-Import moeglich).

    gruen nur, wenn ALLE Kriterien erfuellt sind: Markt-Ampel + Stage-2-Trend
    (Grundvoraussetzung, sonst direkt rot) UND enge Basis/VCP (= geringe
    Volatilitaet VOR dem Einstieg, nicht waehrend der Haltedauer) UND
    Pivot-Trigger (Livermore-Pivotpunkt) UND kein Extended/Earnings/50-80/
    Klimax-Risiko. Strenger als der Signal-Hub-"Top-Setups"-Filter, weil ein
    gehebelter Trade eine engere Stop-Distanz und weniger Fehlertoleranz hat.
    """
    markt_ok = (regime.get(markt) or {}).get("ampel") == "gruen"
    trend_ok = kontext.get("stage2_ampel") == "gruen"
    if not (markt_ok and trend_ok):
        return {"stufe": "rot", "gruende": ["Markt-Ampel oder Stage-2-Trend-Template nicht grün"]}
    basis_ok = kontext.get("basis_ampel") == "gruen"
    trigger_ok = pivot_status in ("ARMED", "BREAKOUT")
    ext = kontext.get("extended_pct")
    ext_ok = not (ext is not None and ext >= 25)
    earn = kontext.get("earnings") or {}
    earn_ok = not (earn.get("status") == "termin" and earn.get("tage") is not None
                   and 0 <= earn["tage"] <= warn_tage)
    gap_ok = not kontext.get("minervini_5080")
    klimax_ok = not kontext.get("klimax_warnung")
    gruende = []
    if not basis_ok: gruende.append("Basis/VCP noch nicht eng genug (Volatilität zu hoch)")
    if not trigger_ok: gruende.append("kein Pivot-Trigger (ARMED/BREAKOUT)")
    if not ext_ok: gruende.append("Extended >25% über SMA50")
    if not earn_ok: gruende.append("Earnings-Fenster")
    if not gap_ok: gruende.append("50/80-Gap-Risiko")
    if not klimax_ok: gruende.append("Klimax-Risiko")
    return {"stufe": "gelb" if gruende else "gruen", "gruende": gruende}


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
    ticker, basis_erstellt, marktregime = lade_tickerliste()
    if not ticker:
        return False

    pivotmap = lade_pivotmap()
    warn_tage = lade_warn_tage()
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
        kontext = t["_hebel_kontext"]
        treffer.append({
            **{k: v for k, v in t.items() if k != "_hebel_kontext"},
            "preis": round(ohlc["closes"][-1], 2),
            "pa_score": pa_score(m),
            "muster": m,
            "chart": _chartdaten(ohlc),
            "hebel_ampel": hebel_ampel(kontext, t["markt"], marktregime,
                                       pivotmap.get(t["ticker"]), warn_tage),
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
