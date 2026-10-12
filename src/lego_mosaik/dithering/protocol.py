"""Schnittstelle für Dithering-Verfahren (Strategy Pattern)."""

from typing import Protocol

from lego_mosaik.domain.models import FloatImage, IndexGrid


class Ditherer(Protocol):
    """Bildet ein Bild im Lab-Raum auf Palettenfarben ab.

    Strategy Pattern: Die Pipeline ruft nur ``dither`` auf und weiß nicht, welches
    Verfahren dahintersteckt. Austauschen = andere Strategy-Instanz übergeben.
    """

    def dither(self, image_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Gibt ein H×W-Raster mit Palettenindizes zurück.

        Args:
            image_lab: Bild als H×W×3-Array im Lab-Raum.
            palette_lab: Palette als P×3-Array im Lab-Raum.
        """
        ...
