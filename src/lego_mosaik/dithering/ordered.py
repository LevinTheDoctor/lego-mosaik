"""Ordered Dithering mit Bayer-Matrix.

Statt Fehler zu verteilen, wird auf jedes Pixel vor der Farbsuche ein fester,
positionsabhängiger Schwellwert-Offset addiert. Die Bayer-Matrix verteilt diese
Offsets so gleichmäßig wie möglich. Das Ergebnis ist ein regelmäßiges Raster, das
bei Mosaiken ruhig und "gewollt" aussieht und keine Fehler über das Bild schleppt.
"""

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.dithering.protocol import ColorSpace
from lego_mosaik.domain.models import FloatImage, IndexGrid

RGB_RANGE = (0.0, 255.0)


def bayer_matrix(size: int) -> npt.NDArray[np.float64]:
    """Normierte Bayer-Matrix der Größe ``size`` × ``size`` mit Werten in [-0.5, 0.5).

    Rekursiver Aufbau: Aus der Matrix M der halben Größe entsteht
    ``[[4M, 4M+2], [4M+3, 4M+1]]``. Das verschachtelt die Schwellwerte so, dass
    benachbarte Zellen möglichst unterschiedliche Werte haben.

    Args:
        size: Kantenlänge, eine Zweierpotenz (1, 2, 4, 8, …).
    """
    # (size & (size - 1)) == 0 gilt genau für Zweierpotenzen: & ist bitweises UND
    if size < 1 or size & (size - 1):
        raise ValueError(f"Bayer-Größe muss eine Zweierpotenz sein, nicht {size}")
    matrix = np.zeros((1, 1), dtype=np.float64)
    while matrix.shape[0] < size:
        # np.block setzt die vier Teilmatrizen zu einer doppelt so großen zusammen
        matrix = np.block([[4 * matrix, 4 * matrix + 2], [4 * matrix + 3, 4 * matrix + 1]])
    # Werte 0..n²-1 → (wert + 0.5) / n² ∈ (0, 1) → minus 0.5 ∈ (-0.5, 0.5), Mittelwert 0
    return (matrix + 0.5) / matrix.size - 0.5


class OrderedDitherer:
    """Bayer-Dithering: Offset auf RGB addieren, dann nächste Palettenfarbe suchen.

    Args:
        color_space: Farbsuche und Umrechnung Lab ↔ RGB.
        size: Kantenlänge der Bayer-Matrix (2, 4 oder 8).
        spread: Stärke des Offsets in RGB-Stufen (0 = kein Dithering).
    """

    def __init__(self, color_space: ColorSpace, *, size: int, spread: float) -> None:
        self._color_space = color_space
        self._thresholds = bayer_matrix(size)
        self._spread = spread

    def dither(self, image_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Gibt ein H×W-Raster mit Palettenindizes zurück."""
        height, width, _ = image_lab.shape
        size = self._thresholds.shape[0]
        # np.tile wiederholt die Matrix kachelartig; "-(-a // b)" ist Aufrunden der Division
        reps = (-(-height // size), -(-width // size))
        offsets = np.tile(self._thresholds, reps)[:height, :width] * self._spread
        rgb = self._color_space.to_rgb(image_lab)
        # offsets[..., np.newaxis]: (H, W) → (H, W, 1), damit derselbe Offset auf R, G und B wirkt
        shifted = np.clip(rgb + offsets[..., np.newaxis], *RGB_RANGE)
        return self._color_space.nearest(self._color_space.to_lab(shifted), palette_lab)
