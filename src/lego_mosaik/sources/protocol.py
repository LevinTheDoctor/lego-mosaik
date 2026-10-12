"""Schnittstelle für Datenquellen des Teilekatalogs."""

from typing import Protocol

from lego_mosaik.domain.models import PartCatalog


class PartCatalogSource(Protocol):
    """Liefert den Teilekatalog.

    ``typing.Protocol`` beschreibt eine Schnittstelle strukturell ("Duck Typing mit
    Typprüfung"): Jede Klasse mit einer passenden ``load_catalog``-Methode erfüllt das
    Protocol, ohne davon erben zu müssen. Später kann so eine PostgreSQL-Quelle die
    CSV-Quelle ersetzen, ohne dass die Pipeline sich ändert.
    """

    def load_catalog(self) -> PartCatalog:
        """Lädt den Katalog (Farben, Teiltypen, verfügbare Kombinationen)."""
        ...
