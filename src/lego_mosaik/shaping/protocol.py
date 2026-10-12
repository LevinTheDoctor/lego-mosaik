"""Schnittstelle für die Formwahl (Strategy Pattern)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from lego_mosaik.domain.models import (
    FloatImage,
    IndexGrid,
    LegoColor,
    PartCatalog,
    Placement,
)


@dataclass(frozen=True, slots=True)
class ShapeResult:
    """Ergebnis der Formwahl (Zeile/Spalte relativ zum übergebenen Raster).

    Attributes:
        parts: Die Formteile. ``bottom_ldu`` ist relativ zur Relief-Höhe der Zelle:
            0 = direkt auf dem Untergrund, eine Plate höher = auf einer Unterlage.
        underlays: Plates unter Formteilen, deren Lücken eine eigene Farbe zeigen sollen
            (wie die Plate-Stapel bei Brick Sketches).
    """

    parts: tuple[Placement, ...] = ()
    underlays: tuple[Placement, ...] = ()


class ShapeRefiner(Protocol):
    """Ersetzt einzelne Noppen durch Formteile, deren Lücken den Untergrund zeigen.

    Attributes:
        subsample: Teilpixel pro Noppe und Richtung, die ``refine`` im Detailbild
            erwartet; 0 heißt, es braucht kein Detailbild.
    """

    subsample: int

    def accepts(self, color: LegoColor, catalog: PartCatalog) -> bool:
        """True, wenn die Farbe in die Palette darf (z. B. „punkte“: nur mit rundem Tile)."""
        ...

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
        """Liefert die Formteile; alle übrigen Zellen zerlegt danach die Zerlegung.

        Args:
            grid: H×W-Raster mit Palettenindizes; ``EMPTY_CELL`` bleibt unberührt.
            detail_lab: Bild mit ``subsample``-facher Auflösung im Lab-Raum
                (H·s × W·s × 3), ``None`` bei ``subsample == 0``.
            palette: Die Farben des Rasters.
            palette_lab: Dieselben Farben im Lab-Raum (P × 3).
            catalog: Verfügbare Teile und Farben.
            levels: H×W-Raster mit der Relief-Höhe jeder Zelle in Plates. Teile über
                mehrere Zellen dürfen nur auf gleich hohe Zellen.
            background_lab: H×W×3, Lab-Farbe dessen, was in den Lücken zu sehen ist.

        Returns:
            Formteile und ihre Unterlagen.
        """
        ...
