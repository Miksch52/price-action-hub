#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Eigenstaendiger Kursdaten-Abruf fuer den Price-Action-Hub.

Adaptiert aus Signal-Hub/src/scorer.py::yahoo_chart() (bewusst dupliziert,
kein Import ueber die Ordnergrenze -- siehe pfade.py-Docstring). Wichtigster
Unterschied zum Original: dort werden High/Low/Open nach der internen
Faktoren-Berechnung verworfen (nur Close+Volumen landen in signals.json);
hier bleiben ALLE Felder erhalten, weil Kerzenmuster ohne sie nicht
erkennbar sind. range=1y statt 2y, weil kein 52-Wochen-Hoch/Tief gebraucht
wird (nur MA200-Kontext + kurzfristige Muster).
"""

import concurrent.futures
import json
import ssl
import time
import urllib.parse
import urllib.request

import pfade

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}


def _http_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15, context=SSL_CTX) as r:
        return json.load(r)


def yahoo_chart(symbol, range_="1y"):
    """Volles OHLCV (kein Reduzieren) + meta. None bei Fehler/zu wenig Historie."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range={range_}&interval=1d")
    d = _http_json(url)
    res = d.get("chart", {}).get("result")
    if not res:
        return None
    r = res[0]
    meta = r.get("meta", {})
    q = r.get("indicators", {}).get("quote", [{}])[0]
    co, vo = q.get("close") or [], q.get("volume") or []
    hi, lo, op = q.get("high") or [], q.get("low") or [], q.get("open") or []
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for i in range(len(co)):
        c = co[i]
        v = vo[i] if i < len(vo) else None
        if c is None or v is None:
            continue
        closes.append(c)
        volumes.append(v)
        highs.append(hi[i] if i < len(hi) and hi[i] is not None else c)
        lows.append(lo[i] if i < len(lo) and lo[i] is not None else c)
        opens.append(op[i] if i < len(op) and op[i] is not None else c)
    if len(closes) < 60:
        return None
    return {"meta": meta, "opens": opens, "highs": highs, "lows": lows,
            "closes": closes, "volumes": volumes}


def lade_cache():
    try:
        with open(pfade.YAHOO_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def speichere_cache(cache):
    with open(pfade.YAHOO_CACHE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def hole_chart_cached(symbol, cache, heute):
    """Tages-Cache-Wrapper. Jeder Netzwerkfehler wird einzeln abgefangen,
    damit ein einzelner problematischer Ticker nicht den ganzen Lauf
    abbricht (analog Signal-Hub::hole_chart_cached). Performance-Review
    2026-08-02: Exceptions werden NICHT gecacht (sonst sperrt ein einmaliger
    Timeout den Ticker fuer den Rest des Tages, ununterscheidbar von "wirklich
    keine Daten") - nur ein echtes Leerergebnis von yahoo_chart() ist legitim."""
    key = f"{symbol}@{heute}"
    if key in cache:
        return cache[key]
    try:
        d = yahoo_chart(symbol)
    except Exception:
        time.sleep(0.25)
        return None
    time.sleep(0.25)  # schont Yahoo, gleiches Lastprofil wie Signal-Hub
    cache[key] = d
    return d


def prefetch_charts_parallel(symbols, cache, heute, max_workers=5):
    """Holt alle noch nicht gecachten Charts parallel statt seriell mit
    time.sleep() dazwischen (Performance-Review 2026-08-02, analog Signal-Hub
    scorer.py::prefetch_charts_parallel). Schreibt in `cache` im selben Format
    wie hole_chart_cached() - danach ist jeder hole_chart_cached()-Aufruf im
    bestehenden Loop ein reiner Cache-Hit, kein Refactoring der Aufrufer noetig."""
    fehlend, gesehen = [], set()
    for symbol in symbols:
        if not symbol or symbol in gesehen:
            continue
        gesehen.add(symbol)
        if f"{symbol}@{heute}" not in cache:
            fehlend.append(symbol)
    if not fehlend:
        return
    def _fetch(symbol):
        try:
            d = yahoo_chart(symbol)
            fehler = False
        except Exception:
            d, fehler = None, True
        time.sleep(0.2)
        return symbol, d, fehler
    print(f"  Praefetch: {len(fehlend)} neue Charts ({max_workers} parallel) ...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for symbol, d, fehler in ex.map(_fetch, fehlend):
            if not fehler:
                cache[f"{symbol}@{heute}"] = d
