"""Stil „quadrat“: keine Formteile, nur eckige Plates und Tiles."""

from collections.abc import Sequence

from lego_mosaik.domain.models import (
    FloatImage,
    IndexGrid,
    LegoColor,
    PartCatalog,
)
from lego_mosaik.shaping.protocol import ShapeResult


class PlainShaper:
    """Lässt alle Noppen der Zerlegung (Null-Objekt, damit die Pipeline nie verzweigen muss)."""

    subsample = 0

    def accepts(self, color: LegoColor, catalog: PartCatalog) -> bool:
        """Jede Farbe der Palette taugt."""
        return True

    def refine(
        self,
        grid: IndexGrid,
        detail_lab: FloatImage | None,
        palette: Sequence[LegoColor],
        palette_lab: FloatImage,
        catalog: PartCatalog,
        *,
        levels: IndexGrid,
        background_lab: FloatImage,
    ) -> ShapeResult:
        """Keine Formteile."""
        return ShapeResult()
