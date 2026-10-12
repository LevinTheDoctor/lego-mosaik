"""Kein Dithering: jedes Pixel bekommt einfach die nächste Palettenfarbe (Referenz)."""

from lego_mosaik.dithering.protocol import ColorSpace
from lego_mosaik.domain.models import FloatImage, IndexGrid


class NearestColorDitherer:
    """Ordnet jedem Pixel unabhängig die nächste Palettenfarbe zu.

    Dient als Referenz: Flächen bleiben ruhig, Farbverläufe zeigen aber harte Stufen.
    """

    def __init__(self, color_space: ColorSpace) -> None:
        self._color_space = color_space

    def dither(self, image_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Gibt ein H×W-Raster mit Palettenindizes zurück."""
        return self._color_space.nearest(image_lab, palette_lab)
