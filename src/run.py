#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Orchestrator fuer den Price-Action-Hub.

Kein --scheduled-Modus (anders als Signal-Hub/src/run.py): das Timing/
Gating (nur laufen, wenn Signal-Hub in diesem Workflow-Lauf frische Daten
geschrieben hat) uebernimmt der aufrufende GitHub-Actions-Schritt per
Datei-Existenz-Check auf Signal-Hub/data/signals.json.

Test: python3 run.py
"""

import sys

import scorer
import top_setups
import katalysator
import katalysator_backtest
import hebel_backtest
import muster_backtest


def main():
    ok = scorer.score_alle()
    # Als letzter Pipeline-Schritt die winzige Startseiten-Zusammenfassung
    # bauen (Score+Pivot+PA-Score+Regime -> Signal-Hub/data/top_setups.js).
    # Reiner JSON-Join, scheitert nie hart (nur kein Panel).
    try:
        top_setups.schreibe()
    except Exception as ex:
        print(f"Top-Setups-Aggregat uebersprungen ({ex}).")
    # Katalysator-Layer (seit 2026-08-28, siehe katalysator.py): braucht die
    # gerade geschriebene top_setups.json, deshalb NACH top_setups.schreibe().
    # Eigenes Tages-Gate (hoechstens 1x/Tag, Kostengrund) - scheitert nie hart,
    # auch ohne gesetztes PERPLEXITY_API_KEY (dann reiner No-op).
    try:
        katalysator.taeglich_falls_faellig()
        katalysator_backtest.log_und_evaluate()
    except Exception as ex:
        print(f"Katalysator-Layer uebersprungen ({ex}).")
    # Forward-Test der Hebel-Ampel (seit 2026-08-16): heutige gruen/gelb-
    # Ticker ins Logbuch + gereifte Picks auswerten. Scheitert nie hart (nur
    # kein Backtest-Panel), analog top_setups.
    if ok:
        try:
            hebel_backtest.log_und_evaluate()
        except Exception as ex:
            print(f"Hebel-Backtest uebersprungen ({ex}).")
        # Forward-Test der Price-Action-Muster (seit 2026-08-16): analog zum
        # Hebel-Backtest, eigenes Logbuch/eigene Ausgabedatei.
        try:
            muster_backtest.log_und_evaluate()
        except Exception as ex:
            print(f"Muster-Backtest uebersprungen ({ex}).")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
