"""Einfachste Zerlegung: jedes Pixel wird ein 1×1-Teil (Referenz)."""

from collections.abc import Sequence

lazy import numpy as np

from lego_mosaik.domain.models import IndexGrid, LegoColor, PartCatalog, PartKind, Placement
from lego_mosaik.tiling.common import single_stud_part, validate_uniform_height


class SingleStudTiling:
    """Belegt jede Noppe mit einem eigenen 1×1-Teil.

    Args:
        part_kinds: Erlaubte Teilarten in Prioritätsreihenfolge.
    """

    def __init__(self, part_kinds: Sequence[PartKind]) -> None:
        self._part_kinds = tuple(part_kinds)

    def tile(
        self, color_grid: IndexGrid, palette: Sequence[LegoColor], catalog: PartCatalog
    ) -> list[Placement]:
        """Zerlegt das Raster in lauter 1×1-Teile (zeilenweise, oben links beginnend)."""
        validate_uniform_height(catalog, self._part_kinds)
        parts = [single_stud_part(catalog, color, self._part_kinds) for color in palette]
        # np.ndenumerate liefert zeilenweise ((zeile, spalte), wert) für jede Zelle
        return [
            Placement(parts[int(index)], palette[int(index)], row, col)
            for (row, col), index in np.ndenumerate(color_grid)
        ]
