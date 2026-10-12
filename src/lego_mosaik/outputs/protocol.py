"""Schnittstelle für Ausgabe-Writer."""

from pathlib import Path
from typing import Protocol

from lego_mosaik.domain.models import Mosaic


class OutputWriter(Protocol):
    """Schreibt eine Ausgabe des Mosaiks in einen Zielordner.

    Attributes:
        name: Registry-Name des Writers, z. B. ``"ldraw"``.
    """

    name: str

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Schreibt die Ausgabe und gibt den Pfad der (Haupt-)Datei zurück."""
        ...
