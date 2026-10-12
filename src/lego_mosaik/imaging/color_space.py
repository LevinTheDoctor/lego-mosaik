"""Farbraum RGB ↔ Lab und Suche der nächsten Palettenfarbe.

Warum Lab? Im RGB-Raum entspricht derselbe Zahlenabstand je nach Farbton ganz
unterschiedlichen wahrgenommenen Unterschieden. Lab ist so gebaut, dass gleiche
Abstände ungefähr gleich stark wahrgenommen werden: L = Helligkeit (0–100),
a = Grün↔Rot, b = Blau↔Gelb.
"""

from collections.abc import Sequence

lazy import numpy as np
lazy import numpy.typing as npt
lazy from skimage.color import deltaE_ciede2000, lab2rgb, rgb2lab

from lego_mosaik.domain.models import ColorDistance, FloatImage, IndexGrid, LegoColor

RGB_MAX = 255.0
# Höchstzahl Pixel pro Block bei der vektorisierten Suche; begrenzt den
# Speicherbedarf der (Pixel × Palette)-Zwischenarrays bei großen Mosaiken
PIXELS_PER_CHUNK = 8192


def rgb_to_lab(rgb: npt.ArrayLike) -> FloatImage:
    """Wandelt RGB-Werte (0–255, Form ``(..., 3)``) in Lab um."""
    # rgb2lab erwartet Werte von 0.0 bis 1.0, daher die Division durch 255
    return np.asarray(rgb2lab(np.asarray(rgb, dtype=np.float64) / RGB_MAX), dtype=np.float64)


def lab_to_rgb(lab: FloatImage) -> FloatImage:
    """Wandelt Lab-Werte in RGB (0.0–255.0, Form ``(..., 3)``) um."""
    return np.asarray(lab2rgb(lab), dtype=np.float64) * RGB_MAX


def palette_to_lab(palette: Sequence[LegoColor]) -> FloatImage:
    """Palette als P×3-Array im Lab-Raum."""
    return rgb_to_lab([color.rgb for color in palette])


def color_distances(
    pixels_lab: FloatImage, palette_lab: FloatImage, distance: ColorDistance
) -> FloatImage:
    """Abstand jedes Pixels zu jeder Palettenfarbe.

    Args:
        pixels_lab: N×3-Array von Lab-Werten.
        palette_lab: P×3-Array von Lab-Werten.
        distance: Formel für den Farbabstand.

    Returns:
        N×P-Array: Zeile i enthält die Abstände von Pixel i zu allen Palettenfarben.
    """
    # Broadcasting: (N, 1, 3) gegen (1, P, 3) ergibt (N, P, 3), also jedes Pixel
    # gepaart mit jeder Palettenfarbe, ohne eine Python-Schleife
    left = pixels_lab[:, np.newaxis, :]
    right = palette_lab[np.newaxis, :, :]
    if distance is ColorDistance.CIEDE2000:
        return np.asarray(deltaE_ciede2000(left, right, channel_axis=-1), dtype=np.float64)
    # CIE76: euklidische Länge des Differenzvektors über die letzte Achse (L, a, b)
    return np.asarray(np.linalg.norm(left - right, axis=-1), dtype=np.float64)


class LabColorSpace:
    """Farbsuche und Umrechnung mit wählbarem Farbabstand.

    Erfüllt das Protocol ``ColorSpace`` aus ``dithering/protocol.py``. So kennen die
    Dithering-Verfahren nur die Schnittstelle, nicht dieses Modul
    (Dependency Inversion: beide hängen von der Abstraktion ab).

    Args:
        distance: Formel für den Farbabstand (CIE76 oder CIEDE2000).
    """

    def __init__(self, distance: ColorDistance) -> None:
        self.distance = distance

    def nearest(self, pixels_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Index der nächsten Palettenfarbe für Lab-Werte beliebiger Form ``(..., 3)``.

        Gerechnet wird blockweise (``PIXELS_PER_CHUNK``), damit große Mosaike mit
        CIEDE2000 nicht Gigabytes an Zwischenspeicher brauchen.

        Returns:
            Array der Form ``(...)``, also ohne die letzte Achse.
        """
        # reshape(-1, 3): alle Pixel untereinander als N×3; -1 heißt "Länge selbst ausrechnen"
        pixels = pixels_lab.reshape(-1, 3)
        indices = np.empty(len(pixels), dtype=np.intp)
        for start in range(0, len(pixels), PIXELS_PER_CHUNK):
            chunk = pixels[start : start + PIXELS_PER_CHUNK]
            distances = color_distances(chunk, palette_lab, self.distance)
            # argmin(axis=1): pro Zeile (Pixel) der Index der kleinsten Distanz
            indices[start : start + len(chunk)] = distances.argmin(axis=1)
        # shape[:-1]: alle Achsen außer der letzten (den drei Lab-Kanälen)
        return indices.reshape(pixels_lab.shape[:-1])

    def to_rgb(self, lab: FloatImage) -> FloatImage:
        """Lab → RGB (0.0–255.0)."""
        return lab_to_rgb(lab)

    def to_lab(self, rgb: FloatImage) -> FloatImage:
        """RGB (0–255) → Lab."""
        return rgb_to_lab(rgb)
