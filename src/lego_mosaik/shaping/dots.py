"""Stile „punkte“ und „noppen“: jede Noppe ein rundes 1 × 1-Teil.

So sehen LEGO-Art-Bilder und Brick Sketches aus: runde Tiles (oder Plates mit Noppe)
auf dunklem Untergrund, der in den Zwickeln dazwischen durchscheint.
"""

from collections.abc import Sequence

lazy import numpy as np

from lego_mosaik.domain.models import (
    EMPTY_CELL,
    FloatImage,
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    PartShape,
    Placement,
)
from lego_mosaik.shaping.protocol import ShapeResult


class DotShaper:
    """Belegt jede Noppe mit einem runden 1 × 1-Teil in der gedithereten Farbe.

    Args:
        kind: ``TILE`` (glatt, „punkte“) oder ``PLATE`` (mit Noppe, „noppen“).
    """

    subsample = 0

    def __init__(self, kind: PartKind) -> None:
        self._kind = kind

    def accepts(self, color: LegoColor, catalog: PartCatalog) -> bool:
        """Nur Farben, in denen es das runde Teil gibt; sonst entstünden eckige Ausreißer."""
        return bool(catalog.parts_in_color(color.id, (self._kind,), (PartShape.ROUND,)))

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
        """Ein rundes Teil pro belegter Zelle (die Palette enthält nur passende Farben)."""
        parts = {
            index: catalog.parts_in_color(color.id, (self._kind,), (PartShape.ROUND,))
            for index, color in enumerate(palette)
        }
        return ShapeResult(
            tuple(
                Placement(parts[int(index)][0], palette[int(index)], row, col)
                for (row, col), index in np.ndenumerate(grid)
                if index != EMPTY_CELL and parts[int(index)]
            )
        )
