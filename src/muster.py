#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Price-Action-Musteranalyse (v1). Reine Erkennungsfunktionen, KEIN I/O
(kein Netzwerk-/Datei-Zugriff) -- Stilvorbild Signal-Hub/src/pivot.py.

Fuenf Muster + ein Kontext-Filter, Formeln abgeleitet aus den PDF-Quellen
in "price action/" (Al Brooks, Rayner Teo, Tom Hougaard):

  1. f_trend_bar      - Trendbar-Staerke (Close-Position, Body-Anteil, Overlap)
  2. f_inside_bar      - Inside Bar (Konsolidierung im Vorbar-Rahmen)
  3. f_bar_counting    - High1/2 / Low1/2 (Korrekturbar-Zaehlung nach Swing)
  4. f_breakout        - Breakout-Staerke / Failed Breakout
  5. f_gap             - Gap + Fill-Tracking
  6. f_marktstadium    - Kontext-Filter (Advancing/Accumulation/Distribution/
                         Declining aus MA200-Stand + Steigung), kein eigener
                         Score-Faktor, steuert nur ob bullische/baerische
                         Signale ueberhaupt gewertet werden.

analysiere(ohlc, i) ist die Sammelfunktion (Vorbild pivot.klassifiziere()).

Erweiterbar: spaetere Muster (Pin-Bar, Engulfing, Keil, Double-Top/Bottom,
S/R-Clustering, Measured Move, HTF-Bestaetigung, Extended-Bar/Climax) kommen
als weitere f_*-Funktionen dazu und werden in analysiere() ergaenzt, ohne
bestehende Funktionen zu aendern.

Test: python3 -c "import muster, kursdaten, time; \
        d=kursdaten.yahoo_chart('AAPL'); \
        print(muster.analysiere(d))"
"""

# --- Parameter (zentral, leicht justierbar) ---------------------------------
STARKER_CLOSE_POS = 0.90   # close_pos-Schwelle fuer starken Bar-Close (bull)
STARKER_BODY_RATIO = 0.60  # body/range-Schwelle fuer starken Bar

SWING_FENSTER = 3          # +/- Bars fuer lokales Swing-Hoch/Tief
ZUVERLAESSIG_MAX = 2        # High1/High2 bzw. Low1/Low2 gelten als zuverlaessig

BREAKOUT_FENSTER = 20       # Handelstage Referenz fuer neues Hoch/Tief (~1 Monat)
VOLUMEN_FENSTER = 20        # Referenz-Volumenschnitt fuer Volumen-Ratio
FAILED_BREAKOUT_M = 5        # Bars, binnen derer ein Ausbruch als gescheitert gilt

GAP_FILL_FENSTER = 3         # Folgebars, in denen ein Gap-Fill noch gezaehlt wird

# Bewusst 21 (nicht der PDF-Wert 20), damit das MA200-Steigungsfenster
# konsistent zum bestehenden f_stage2-Lookback in Signal-Hub/src/scorer.py
# ist -- sonst wuerden beide Apps scheinbar widerspruechliche
# Marktstadium-Aussagen mit unterschiedlichem Fenster treffen.
MA200_SLOPE_FENSTER = 21
MA200_FENSTER = 200


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _sma(closes, n, ende):
    """SMA(n) mit letztem Index `ende` (exklusiv der Zukunft). None wenn zu wenig Historie."""
    if ende - n < 0:
        return None
    fenster = closes[ende - n:ende]
    return sum(fenster) / n


def f_trend_bar(o, h, l, c, o_prev, h_prev, l_prev, c_prev):
    """Trendbar-Staerke: Close-Position in der Range, Body-Anteil, Overlap
    zur Vorbar. Kleiner Overlap + starker Close = klares Trendzeichen."""
    rng = h - l
    if rng <= 0:
        return {"close_pos": None, "body_ratio": None, "overlap": None,
                "richtung": None, "stark": False}
    close_pos = (c - l) / rng
    body_ratio = abs(c - o) / rng
    rng_prev = h_prev - l_prev
    overlap = (max(0.0, min(h, h_prev) - max(l, l_prev)) / rng_prev) if rng_prev > 0 else None
    richtung = "bull" if c >= o else "bear"
    stark = (
        (close_pos > STARKER_CLOSE_POS and richtung == "bull" and body_ratio > STARKER_BODY_RATIO)
        or (close_pos < (1 - STARKER_CLOSE_POS) and richtung == "bear" and body_ratio > STARKER_BODY_RATIO)
    )
    return {"close_pos": round(close_pos, 3), "body_ratio": round(body_ratio, 3),
            "overlap": round(overlap, 3) if overlap is not None else None,
            "richtung": richtung, "stark": stark}


def f_inside_bar(h, l, h_prev, l_prev):
    """Inside Bar: aktuelle Bar bleibt komplett im Rahmen der Vorbar (Mother Bar)."""
    inside = h <= h_prev and l >= l_prev
    return {"inside": inside, "mother_high": h_prev if inside else None,
            "mother_low": l_prev if inside else None}


def f_bar_counting(highs, lows, i):
    """High1/2/3+ bzw. Low1/2/3+: Swing-Erkennung (lokales Extrem ueber
    +/-SWING_FENSTER) + Vorwaertszaehlung der Korrekturbars danach. Nur
    High1/High2 bzw. Low1/Low2 gelten als zuverlaessige Fortsetzungssignale."""
    n = len(highs)
    if i < SWING_FENSTER or i >= n:
        return {"typ": None, "seit_swing_bars": None, "zuverlaessig": False}

    # Letztes Swing-Hoch bzw. -Tief VOR der aktuellen Bar suchen.
    swing_high_idx = swing_low_idx = None
    for j in range(i - SWING_FENSTER, -1, -1):
        lo_j, hi_j = max(0, j - SWING_FENSTER), min(n, j + SWING_FENSTER + 1)
        if swing_high_idx is None and highs[j] == max(highs[lo_j:hi_j]):
            swing_high_idx = j
        if swing_low_idx is None and lows[j] == min(lows[lo_j:hi_j]):
            swing_low_idx = j
        if swing_high_idx is not None and swing_low_idx is not None:
            break

    def _zaehle(swing_idx, arr, aufwaerts):
        if swing_idx is None:
            return None
        n_bars = 0
        for k in range(swing_idx + 1, i + 1):
            neu = arr[k] > arr[k - 1] if aufwaerts else arr[k] < arr[k - 1]
            if neu:
                n_bars += 1
        return n_bars if n_bars > 0 else None

    high_n = _zaehle(swing_high_idx, highs, True)
    low_n = _zaehle(swing_low_idx, lows, False)

    # Naeher liegender Swing gewinnt (der aktuell relevantere Kontext).
    kandidat = None
    if high_n is not None and (low_n is None or swing_high_idx >= swing_low_idx):
        kandidat = ("High", high_n, i - swing_high_idx)
    elif low_n is not None:
        kandidat = ("Low", low_n, i - swing_low_idx)

    if not kandidat:
        return {"typ": None, "seit_swing_bars": None, "zuverlaessig": False}
    praefix, n_bars, seit = kandidat
    typ = f"{praefix}{n_bars}" if n_bars <= ZUVERLAESSIG_MAX else f"{praefix}{ZUVERLAESSIG_MAX + 1}+"
    return {"typ": typ, "seit_swing_bars": seit, "zuverlaessig": n_bars <= ZUVERLAESSIG_MAX}


def f_breakout(closes, highs, lows, volumes, i):
    """Breakout-Staerke / Failed Breakout gegen ein N-Tage-Referenzlevel."""
    if i < BREAKOUT_FENSTER or i < VOLUMEN_FENSTER:
        return {"breakout_up": False, "breakout_down": False, "staerke": None, "failed": None}

    ref_high = max(highs[i - BREAKOUT_FENSTER:i])
    ref_low = min(lows[i - BREAKOUT_FENSTER:i])
    breakout_up = closes[i] > ref_high
    breakout_down = closes[i] < ref_low

    vol_basis = _mean(volumes[i - VOLUMEN_FENSTER:i])
    vol_ratio = (volumes[i] / vol_basis) if vol_basis else None
    rng = highs[i] - lows[i]
    body_ratio = (abs(closes[i] - (highs[i] + lows[i]) / 2) / rng) if rng > 0 else None
    staerke = {"body_ratio": round(body_ratio, 3) if body_ratio is not None else None,
               "vol_ratio": round(vol_ratio, 2) if vol_ratio is not None else None} \
        if (breakout_up or breakout_down) else None

    failed = None
    if breakout_up:
        spaeter = closes[i + 1:i + 1 + FAILED_BREAKOUT_M]
        failed = any(x < ref_high for x in spaeter) if spaeter else None
    elif breakout_down:
        spaeter = closes[i + 1:i + 1 + FAILED_BREAKOUT_M]
        failed = any(x > ref_low for x in spaeter) if spaeter else None

    return {"breakout_up": breakout_up, "breakout_down": breakout_down,
            "staerke": staerke, "failed": failed}


def f_gap(opens, highs, lows, closes, i):
    """Opening Gap + Fill-Tracking ueber die folgenden GAP_FILL_FENSTER Bars."""
    if i < 1:
        return {"gap_up": False, "gap_down": False, "gap_pct": None,
                "gefuellt": None, "gefuellt_nach_bars": None}

    gap_up = opens[i] > highs[i - 1]
    gap_down = opens[i] < lows[i - 1]
    gap_pct = None
    if gap_up and closes[i - 1]:
        gap_pct = round((opens[i] - closes[i - 1]) / closes[i - 1] * 100, 2)
    elif gap_down and closes[i - 1]:
        gap_pct = round((opens[i] - closes[i - 1]) / closes[i - 1] * 100, 2)

    gefuellt = gefuellt_nach = None
    if gap_up or gap_down:
        gefuellt = False
        n = len(lows)
        for k in range(i, min(i + GAP_FILL_FENSTER, n)):
            if gap_up and lows[k] <= highs[i - 1]:
                gefuellt, gefuellt_nach = True, k - i
                break
            if gap_down and highs[k] >= lows[i - 1]:
                gefuellt, gefuellt_nach = True, k - i
                break

    return {"gap_up": gap_up, "gap_down": gap_down, "gap_pct": gap_pct,
            "gefuellt": gefuellt, "gefuellt_nach_bars": gefuellt_nach}


def f_marktstadium(closes, i):
    """Kontext-Filter: Advancing/Accumulation/Distribution/Declining aus
    MA200-Stand + Steigung ueber MA200_SLOPE_FENSTER Tage."""
    ma200 = _sma(closes, MA200_FENSTER, i + 1)
    ma200_alt = _sma(closes, MA200_FENSTER, i + 1 - MA200_SLOPE_FENSTER)
    if ma200 is None or ma200_alt is None:
        return {"stadium": None, "ma200": None, "slope": None}
    slope = ma200 - ma200_alt
    preis = closes[i]
    if preis > ma200 and slope > 0:
        stadium = "Advancing"
    elif preis > ma200 and slope <= 0:
        stadium = "Accumulation"
    elif preis < ma200 and slope >= 0:
        stadium = "Distribution"
    else:
        stadium = "Declining"
    return {"stadium": stadium, "ma200": round(ma200, 2), "slope": round(slope, 4)}


def analysiere(ohlc, i=None):
    """Sammelfunktion: wertet einen Bar-Index (Default: letzter verfuegbarer
    Tag) mit allen sechs Funktionen aus. ohlc = {"opens","highs","lows",
    "closes","volumes"} gleich lange Listen (Vorbild pivot.klassifiziere())."""
    o, h, l, c, v = (ohlc.get(k) or [] for k in ("opens", "highs", "lows", "closes", "volumes"))
    n = len(c)
    if i is None:
        i = n - 1
    if i < 0:
        i = n + i
    if n < 60 or i < 1 or i >= n:
        return {"status": "-", "grund": "zu wenig Historie"}

    trend_bar = f_trend_bar(o[i], h[i], l[i], c[i], o[i - 1], h[i - 1], l[i - 1], c[i - 1])
    inside_bar = f_inside_bar(h[i], l[i], h[i - 1], l[i - 1])
    bar_counting = f_bar_counting(h, l, i)
    breakout = f_breakout(c, h, l, v, i)
    gap = f_gap(o, h, l, c, i)
    marktstadium = f_marktstadium(c, i)

    return {
        "status": "ok",
        "trend_bar": trend_bar,
        "inside_bar": inside_bar,
        "bar_counting": bar_counting,
        "breakout": breakout,
        "gap": gap,
        "marktstadium": marktstadium,
    }
