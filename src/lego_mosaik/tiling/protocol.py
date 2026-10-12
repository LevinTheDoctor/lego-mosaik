"""Schnittstelle für Zerlegungsstrategien (Strategy Pattern)."""

from collections.abc import Sequence
from typing import Protocol

from lego_mosaik.domain.models import IndexGrid, LegoColor, PartCatalog, Placement


class TilingStrategy(Protocol):
    """Fasst gleichfarbige Pixel zu real existierenden Teilen zusammen."""

    def tile(
        self, color_grid: IndexGrid, palette: Sequence[LegoColor], catalog: PartCatalog
    ) -> list[Placement]:
        """Zerlegt das Farbraster vollständig und überlappungsfrei in Teile.

        Args:
            color_grid: H×W-Raster mit Indizes in ``palette``.
            palette: Die Farben, auf die sich das Raster bezieht.
            catalog: Katalog mit verfügbaren (Teil, Farbe)-Kombinationen.
        """
        ...
