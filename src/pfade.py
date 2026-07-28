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
Datenaustausch (Tickerliste, keine Scores) ist erlaubt.
"""

import os

HIER = os.path.dirname(os.path.abspath(__file__))       # .../Price-Action-Hub/src
PROJEKT = os.path.dirname(HIER)                           # .../Price-Action-Hub
REPO_ROOT = os.path.dirname(PROJEKT)                       # Projekt-Root

DATA = os.path.join(PROJEKT, "data")
os.makedirs(DATA, exist_ok=True)

PRICEACTION_JSON = os.path.join(DATA, "priceaction.json")
PRICEACTION_JS = os.path.join(DATA, "priceaction.js")

# Reiner Datenpfad auf die Signal-Hub-Ausgabe (nur Ticker/Name/Markt gefragt,
# keine Scores). Existiert in einem frischen GitHub-Actions-Checkout nur,
# wenn der Signal-Hub-Schritt in genau diesem Workflow-Lauf tatsaechlich
# geschrieben hat (Signal-Hub/data/ ist komplett .gitignore't).
SIGNAL_HUB_SIGNALS_JSON = os.path.join(REPO_ROOT, "Signal-Hub", "data", "signals.json")

LOKAL = os.path.expanduser("~/Library/Application Support/PriceActionHub")
os.makedirs(LOKAL, exist_ok=True)
YAHOO_CACHE = os.path.join(LOKAL, "yahoo_cache.json")
