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


class ColorSpace(Protocol):
    """Farbsuche und Farbraum-Umrechnung, die Dithering-Verfahren benötigen.

    Die Implementierung (``imaging.color_space.LabColorSpace``) wird von außen
    übergeben. So hängt ``dithering`` nicht von ``imaging`` ab.
    """

    def nearest(self, pixels_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Index der nächsten Palettenfarbe für Lab-Werte der Form ``(..., 3)``."""
        ...

    def to_rgb(self, lab: FloatImage) -> FloatImage:
        """Lab → RGB (0.0–255.0)."""
        ...

    def to_lab(self, rgb: FloatImage) -> FloatImage:
        """RGB (0–255) → Lab."""
        ...
