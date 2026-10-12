"""Composition Root: einzige Stelle, an der konfiguriert und verdrahtet wird.

Die vollständige Konfiguration und Verdrahtung folgt, sobald Pipeline und CLI
existieren. Bis dahin gibt es nur den Einstiegspunkt aus ``pyproject.toml``.
"""

import sys


def run() -> None:
    """Entry Point aus pyproject.toml: startet die deutsche CLI."""
    # sys.exit mit einem String gibt die Meldung auf stderr aus und beendet mit Code 1
    sys.exit("Die CLI ist noch nicht implementiert.")
