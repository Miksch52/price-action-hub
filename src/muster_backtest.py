#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Forward-Test für die Price-Action-Muster (muster.py) und den daraus
abgeleiteten pa_score.

Gleiches Grundprinzip wie hebel_backtest.py (unverzerrtes Forward-Logbuch,
kein Retro-Modus - eine historische Rekonstruktion aller sechs Muster
rückwirkend lohnt sich für einen ersten Baustein nicht). Sechs Kohorten,
die typischste erste Frage eines Price-Action-Traders beantwortend "bringt
DIESES Muster tatsächlich einen Forward-Return":

  breakout_up           - frischer Ausbruch nach oben (neues Hoch vs. 20-Tage-
                           Referenz). BEWUSST ohne muster.py's eigenes "failed"-
                           Flag als Vorfilter: das reift erst ueber die NAECHSTEN
                           5 Bars (muster.py::f_breakout, kein Lookahead) und ist
                           am Signaltag selbst immer None - der 4/8/12-Wochen-
                           Forward-Return HIER ist die eigentliche, spaetere
                           Antwort auf "hat der Ausbruch gehalten".
  breakout_down          - frischer Ausbruch nach unten (analog)
  trendbar_stark_bull   - starke bullische Trendbar (Close nahe Hoch + großer Body)
  trendbar_stark_bear   - starke bärische Trendbar
  score_bullisch        - pa_score >= 2 (dieselbe Schwelle wie die Standardansicht
                           im Dashboard, siehe price-action-hub.html::einstufFor())
  score_baerisch        - pa_score <= -2

  python3 src/muster_backtest.py --log       # haengt heutige Treffer je
        Kohorte mit Datum+Kurs ans Forward-Logbuch.
  python3 src/muster_backtest.py --evaluate  # bewertet gereifte Picks
        (>=21/50/78 Kalendertage) gegen aktuelle Yahoo-Kurse.

run.py ruft log_und_evaluate() bei jedem Lauf auf.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import index_vergleich
import pfade
import kursdaten

HORIZONTE = [("4W", 21), ("8W", 50), ("12W", 78)]   # Mindest-KALENDERtage je Kohorte
KOHORTEN = ("breakout_up", "breakout_down",
            "trendbar_stark_bull", "trendbar_stark_bear",
            "score_bullisch", "score_baerisch")
SCORE_SCHWELLE = 2   # identisch zu price-action-hub.html::einstufFor()


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


def _kohorten_fuer(t):
    """Welche Kohorten trifft dieser Treffer heute (ein Ticker kann mehrere
    gleichzeitig treffen, z.B. breakout_bestaetigt UND score_bullisch)."""
    m = t.get("muster") or {}
    out = []
    bo = m.get("breakout") or {}
    if bo.get("breakout_up"):
        out.append("breakout_up")
    if bo.get("breakout_down"):
        out.append("breakout_down")
    tb = m.get("trend_bar") or {}
    if tb.get("stark") and tb.get("richtung") == "bull":
        out.append("trendbar_stark_bull")
    if tb.get("stark") and tb.get("richtung") == "bear":
        out.append("trendbar_stark_bear")
    pa = t.get("pa_score")
    if pa is not None and pa >= SCORE_SCHWELLE:
        out.append("score_bullisch")
    if pa is not None and pa <= -SCORE_SCHWELLE:
        out.append("score_baerisch")
    return out


# ---------------------------------------------------------------------------
def _logbuch_load():
    if os.path.exists(pfade.MUSTER_LOGBUCH):
        try:
            return json.load(open(pfade.MUSTER_LOGBUCH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _logbuch_save(lb):
    with open(pfade.MUSTER_LOGBUCH, "w", encoding="utf-8") as f:
        json.dump(lb, f, ensure_ascii=False, indent=2)


def log_heute():
    if not os.path.exists(pfade.PRICEACTION_JSON):
        print("Keine priceaction.json -> nichts zu loggen.")
        return
    daten = json.load(open(pfade.PRICEACTION_JSON, encoding="utf-8"))
    heute = datetime.now().strftime("%Y-%m-%d")
    lb = _logbuch_load()
    bekannt = {(e["datum"], e["ticker"], e["kohorte"]) for e in lb}
    neu = 0
    for t in daten.get("treffer", []):
        preis = t.get("preis")
        if not preis or not t.get("ticker"):
            continue
        for kohorte in _kohorten_fuer(t):
            key = (heute, t["ticker"], kohorte)
            if key in bekannt:
                continue
            lb.append({
                "datum": heute, "ticker": t["ticker"],
                "yahoo_symbol": t.get("yahoo_symbol"), "markt": t.get("markt"),
                "kohorte": kohorte, "preis_signal": preis,
            })
            neu += 1
    # Aufbewahrung nach ALTER statt nach Eintragszahl (seit 2026-09-13). Die
    # fruehere Kappe "lb[-N:]" skalierte mit der Treffermenge: bei ~417
    # Hebel- bzw. ~172 Pivot-Eintraegen je Tag behielt sie in der Cloud nur
    # 12 bzw. 29 Tage und loeschte Episoden, bevor sie 4W/8W/12W erreichen
    # konnten (Hebel-Backtest dauerhaft n=0, Pivot nie 8W/12W; Pivot-
    # Episoden 02.07.-15.08.2026 dadurch unwiederbringlich verloren).
    # 120 Tage = laengster Horizont (78 Kalendertage) plus Puffer.
    aufbewahrung_tage = 120
    grenze = (datetime.now().date() - timedelta(days=aufbewahrung_tage)).isoformat()
    lb = [e for e in lb if (e.get("datum") or "") >= grenze]
    _logbuch_save(lb)
    print(f"Muster-Forward-Logbuch: {neu} neue Picks ergaenzt (gesamt {len(lb)}).")


# ---------------------------------------------------------------------------
def evaluate():
    lb = _logbuch_load()
    if not lb:
        print("Muster-Logbuch leer -> erst --log sammeln lassen.")
        return {}, []
    cache = kursdaten.lade_cache()
    heute_dt = datetime.now().date()
    heute_str = heute_dt.isoformat()
    eimer = {k: {h: [] for h, _ in HORIZONTE} for k in KOHORTEN}
    # Index-Vergleich (seit 2026-09-12, Systempruefung Punkt 5): ohne Abzug
    # des Leitindex sagt eine Kohorten-Trefferquote nichts ueber das Muster,
    # nur ueber den Markt im selben Zeitraum.
    eimer_edge = {k: {h: [] for h, _ in HORIZONTE} for k in KOHORTEN}
    idx_charts = index_vergleich.lade_index_charts(
        kursdaten.hole_chart_cached, cache, heute_str)
    einzelfaelle = []
    charts = {}
    for e in lb:
        try:
            tage = (heute_dt - datetime.strptime(e["datum"], "%Y-%m-%d").date()).days
        except Exception:
            continue
        if e.get("kohorte") not in eimer:
            continue
        sym = e.get("yahoo_symbol") or e.get("ticker")
        if sym not in charts:
            charts[sym] = kursdaten.hole_chart_cached(sym, cache, heute_str) or {}
        # Feste Fenster (seit 2026-09-13, Systempruefung Punkt 2), siehe
        # index_vergleich.fenster_returns: Signalkurs gegen den Schlusskurs genau
        # 21/50/78 Kalendertage spaeter. Die Episode zaehlt in jedem erreichten
        # Horizont; bk/ret meinen den laengsten davon (Einzelfall-Liste).
        rets = index_vergleich.fenster_returns(charts[sym], e["datum"], e.get("preis_signal"))
        bk, ret = index_vergleich.laengster_horizont(rets)
        if bk is None:
            continue
        edges = index_vergleich.fenster_edges(idx_charts, e.get("markt"), e["datum"], rets,
                                              pick_chart=charts[sym], ticker=sym)
        for h, r in rets.items():
            if r is None:
                continue
            eimer[e["kohorte"]][h].append(r)
            if edges[h] is not None:
                eimer_edge[e["kohorte"]][h].append(edges[h])
        edge = edges[bk]
        einzelfaelle.append({
            "ticker": e["ticker"], "yahoo_symbol": sym, "kohorte": e["kohorte"],
            "datum": e["datum"], "preis_signal": e["preis_signal"],
            "horizont": bk, "return_pct": round(ret * 100, 2),
            "edge_idx_pct": round(edge * 100, 2) if edge is not None else None,
            "fenster": {h: round(r * 100, 2) for h, r in rets.items() if r is not None},
        })
    kursdaten.speichere_cache(cache)
    fr = {k: {h: index_vergleich.ergaenze_edge(_stats(eimer[k][h]), eimer_edge[k][h])
              for h, _ in HORIZONTE} for k in eimer}
    return fr, einzelfaelle


# ---------------------------------------------------------------------------
def _schreibe(out):
    with open(pfade.MUSTER_BACKTEST, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    with open(pfade.MUSTER_BACKTEST_JS, "w", encoding="utf-8") as f:
        f.write("window.MUSTER_BACKTEST_DATA = ")
        json.dump(out, f, ensure_ascii=False)
        f.write(";")


def _druck_tabelle(fr):
    print(f"{'Kohorte':22s}{'Hor':5s}{'n':>5s}{'Win%':>7s}{'Ø%':>8s}{'ØvsIdx':>9s}{'>Idx%':>7s}")
    for kohorte in KOHORTEN:
        for label, _ in HORIZONTE:
            s = fr.get(kohorte, {}).get(label) or {}
            if not s.get("n"):
                continue
            # Index-Spalten (seit 2026-09-12), siehe rotation_backtest.py
            e_avg = s.get("edge_idx_avg")
            e_win = s.get("edge_idx_win")
            print(f"{kohorte:22s}{label:5s}{s['n']:5d}{s['win']:7.1f}{s['avg']:8.2f}"
                  f"{(e_avg if e_avg is not None else 0):+9.2f}"
                  f"{(e_win if e_win is not None else 0):7.1f}")


def log_und_evaluate():
    """Bequemer Einstiegspunkt fuer run.py: loggen + auswerten + schreiben in
    einem Aufruf."""
    log_heute()
    fr, einzelfaelle = evaluate()
    out = {
        "erstellt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hinweis": ("Forward-Test mit festen Fenstern (seit 2026-09-13): Kurs am Signaltag "
                    "(Muster-Kohorte, siehe Docstring in muster_backtest.py) vs. Schlusskurs "
                    "genau 21/50/78 Kalendertage spaeter; eine Episode zaehlt in jedem "
                    "erreichten Horizont, ihr Wert bleibt danach fest. Unverzerrt "
                    "(Einstufung stand vor dem Ergebnis fest). Ein Ticker kann an einem Tag "
                    "mehrere Kohorten gleichzeitig treffen (z.B. Breakout UND Score bullisch)."),
        "forward_realisiert": fr,
        "forward_einzelfaelle": einzelfaelle,
    }
    _schreibe(out)
    if einzelfaelle:
        print(f"\n=== Price-Action-Muster Forward-Test ({len(einzelfaelle)} gereifte Einzelfaelle) ===")
        _druck_tabelle(fr)
    print(f"Gespeichert: {pfade.MUSTER_BACKTEST}")
    return fr, einzelfaelle


def main():
    args = sys.argv[1:]
    if "--log" in args:
        log_heute()
        return
    if "--evaluate" in args:
        log_und_evaluate()
        return
    print("Nutzung: muster_backtest.py --log | --evaluate")


if __name__ == "__main__":
    main()
