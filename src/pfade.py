#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Zentrale Pfade fuer den Price-Action-Hub.

Trennt bewusst:
  DATA  = Price-Action-Hub/data  -> Dashboard-Ausgabe (priceaction.json/js).
          Liegt in iCloud; wird pro Lauf 1x geschrieben.
  LOKAL = ~/Library/Application Support/PriceActionHub
          -> Yahoo-Cache. NICHT in iCloud (Sync-Konflikte/Eviction bei
          haeufigem Schreiben) und bewusst ein EIGENER Cache-Ordner, nicht
          der von Signal-Hub -> keine Schreibkonflikte zwischen den beiden
          unabhaengigen Apps.

Der Price-Action-Hub liest die Ticker-Universe des Signal-Hub NUR ueber
dessen Ausgabedatei (SIGNAL_HUB_SIGNALS_JSON) -- niemals per Python-Import
aus Signal-Hub/src. Das ist Absicht (Entflechtungsprinzip, CLAUDE.md: "Apps
nicht mergen"): beide Tools bleiben unabhaengig lauffaehig, nur der
Datenaustausch ueber fertige Ausgabedateien ist erlaubt (kein Cross-App-
Python-Import, gleiches Prinzip wie top_setups.py).

Seit der Hebel-Ampel (2026-08-16, scorer.py::hebel_ampel) bewusst erweiterte
Ausnahme: zusaetzlich zur reinen Tickerliste werden aus SIGNAL_HUB_SIGNALS_JSON
gezielt ein paar bereits FERTIG berechnete Ampel-/Risikofelder je Ticker
uebernommen (Stage-2-Trend, Basis/VCP, Extended, Earnings, 50/80, Klimax) plus
SIGNAL_HUB_PIVOT_JSON fuer den Pivot-Status - reines Durchreichen, KEINE
Neuberechnung/kein Ersatz des Signal-Hub-Scores (der bleibt "pa_score", siehe
scorer.py-Docstring). Ohne dieses gezielte Feld-Durchreichen muesste die
Trend-Template-/Markt-Ampel-Logik ein zweites Mal in diesem Repo entstehen -
genau das soll die Entflechtung verhindern (Inhalte leben an einem Ort).
"""

import os

HIER = os.path.dirname(os.path.abspath(__file__))       # .../Price-Action-Hub/src
PROJEKT = os.path.dirname(HIER)                           # .../Price-Action-Hub
REPO_ROOT = os.path.dirname(PROJEKT)                       # Projekt-Root

DATA = os.path.join(PROJEKT, "data")
os.makedirs(DATA, exist_ok=True)

PRICEACTION_JSON = os.path.join(DATA, "priceaction.json")
PRICEACTION_JS = os.path.join(DATA, "priceaction.js")
HEBEL_BACKTEST = os.path.join(DATA, "hebel_backtest.json")  # Forward-Test der Hebel-Ampel
HEBEL_BACKTEST_JS = os.path.join(DATA, "hebel_backtest.js")  # file://-Fallback
MUSTER_BACKTEST = os.path.join(DATA, "muster_backtest.json")  # Forward-Test der Price-Action-Muster
MUSTER_BACKTEST_JS = os.path.join(DATA, "muster_backtest.js")  # file://-Fallback

# Reiner Datenpfad auf die Signal-Hub-Ausgabe (nur Ticker/Name/Markt gefragt,
# keine Scores). Existiert in einem frischen GitHub-Actions-Checkout nur,
# wenn der Signal-Hub-Schritt in genau diesem Workflow-Lauf tatsaechlich
# geschrieben hat (Signal-Hub/data/ ist komplett .gitignore't).
SIGNAL_HUB_SIGNALS_JSON = os.path.join(REPO_ROOT, "Signal-Hub", "data", "signals.json")

# Fuer die Hebel-Ampel: Pivot-Status (ARMED/BREAKOUT, Livermore-Pivotpunkt) je
# Ticker - gleiche Datei, die top_setups.py schon liest. Existiert nur, wenn
# der Signal-Hub-Pivot-Screener in diesem Lauf tatsaechlich geschrieben hat.
SIGNAL_HUB_PIVOT_JSON = os.path.join(REPO_ROOT, "Signal-Hub", "data", "pivot.json")

# Gleiches Prinzip fuer den ntfy-Kanal des Top-Setups-Push (top_setups.py):
# reiner Datei-Read von Signal-Hub/config.json, kein Cross-App-Python-Import
# (siehe mts_alarms.py im Hauptrepo fuer dasselbe Muster). Lokal existiert
# die Datei als Geschwister-Ordner immer; im Cloud-Job schreibt ein eigener
# Pipeline-Schritt sie aus dem SIGNALHUB_CONFIG_JSON-Secret dorthin.
SIGNAL_HUB_CONFIG = os.path.join(REPO_ROOT, "Signal-Hub", "config.json")

LOKAL = os.path.expanduser("~/Library/Application Support/PriceActionHub")
os.makedirs(LOKAL, exist_ok=True)
YAHOO_CACHE = os.path.join(LOKAL, "yahoo_cache.json")
# Anti-Spam-Zustand des Top-Setups-Push (welche Ticker waren beim letzten
# Lauf schon in der Schnittmenge) - im Cloud-Job per R2 gesichert, sonst wie
# yahoo_cache.json bei jedem frischen Checkout leer.
TOP_SETUPS_STATE = os.path.join(LOKAL, "top_setups_state.json")
# Forward-Log der Hebel-Ampel (welche Ticker waren wann "gruen"/"gelb", zu
# welchem Kurs) - Basis fuer hebel_backtest.py. Wie top_setups_state.json per
# R2 gesichert (Cloud-Job-Runner sind pro Lauf frisch, siehe pipeline.yml im
# Signal-Hub-Repo) - anders als bei Signal-Hub gibt es hier KEINEN lokalen
# Mac-mini-Dauerlauf (Price-Action-Hub hat keinen eigenen LaunchAgent), die
# R2-Sicherung ist hier also von Anfang an die EINZIGE Persistenz, kein
# nachtraeglicher Fix noetig.
HEBEL_LOGBUCH = os.path.join(LOKAL, "hebel_logbuch.json")
# Forward-Log der Price-Action-Muster (Breakout/Trendbar/Score-Einstufung je
# Ticker, zu welchem Kurs) - Basis fuer muster_backtest.py. Gleiches
# Persistenz-Prinzip wie HEBEL_LOGBUCH (R2 von Anfang an, kein lokaler
# Dauerlauf als Ausweichquelle).
MUSTER_LOGBUCH = os.path.join(LOKAL, "muster_logbuch.json")

# Katalysator-Layer (seit 2026-08-28, siehe katalysator.py): braucht die
# fertige Top-Setups-Liste (top_setups.py schreibt sie VOR diesem Schritt,
# siehe run.py) als Eingabe - reiner Datei-Read, kein Cross-App-Import.
SIGNAL_HUB_TOP_SETUPS_JSON = os.path.join(REPO_ROOT, "Signal-Hub", "data", "top_setups.json")
KATALYSATOR_JSON = os.path.join(REPO_ROOT, "Signal-Hub", "data", "top_setups_katalysator.json")
KATALYSATOR_JS = os.path.join(REPO_ROOT, "Signal-Hub", "data", "top_setups_katalysator.js")
KATALYSATOR_BACKTEST = os.path.join(REPO_ROOT, "Signal-Hub", "data", "katalysator_backtest.json")
KATALYSATOR_BACKTEST_JS = os.path.join(REPO_ROOT, "Signal-Hub", "data", "katalysator_backtest.js")
# Ein-Lauf-pro-Tag-Gate (Kostengrund, siehe katalysator.py-Docstring) - lokal,
# im Cloud-Job wie TOP_SETUPS_STATE/HEBEL_LOGBUCH per R2 gesichert, sonst
# riefe jeder der vier taeglichen Cloud-Slots die kostenpflichtige API auf.
KATALYSATOR_STATE = os.path.join(LOKAL, "katalysator_state.json")
# Forward-Log: welche Top-Setup-Ticker hatten an welchem Tag einen erkannten
# Katalysator (und welche nicht - die Kontrollgruppe), zu welchem Kurs -
# Basis fuer katalysator_backtest.py. Gleiches Persistenz-Prinzip wie
# HEBEL_LOGBUCH/MUSTER_LOGBUCH.
KATALYSATOR_LOGBUCH = os.path.join(LOKAL, "katalysator_logbuch.json")
