"""Fehlerdiffusion (Floyd-Steinberg, Atkinson, Sierra Lite) mit *einer* Klasse.

Idee: Beim Runden eines Pixels auf die nächste Palettenfarbe entsteht ein Fehler
(gewünschte minus gewählte Farbe). Dieser Fehler wird anteilig auf noch nicht
bearbeitete Nachbarpixel verteilt. Im Mittel stimmt die Farbe dadurch, obwohl
jedes einzelne Pixel nur eine Palettenfarbe hat.

Die Verfahren unterscheiden sich nur darin, *wohin* und *wie stark* der Fehler
fließt. Das steckt im ``DiffusionKernel`` als reine Daten. Neue Verfahren
brauchen also nur einen neuen Kernel, keinen neuen Code (DRY).
"""

import logging
from dataclasses import dataclass

lazy import numpy as np

from lego_mosaik.dithering.protocol import ColorSpace
from lego_mosaik.domain.models import FloatImage, IndexGrid

logger = logging.getLogger(__name__)

# Gültige Bereiche im Lab-Raum; nach dem Aufaddieren von Fehlern wird darauf begrenzt,
# damit sich Fehler an Bildstellen ohne passende Palettenfarbe nicht aufschaukeln
L_RANGE = (0.0, 100.0)
AB_RANGE = (-128.0, 127.0)


@dataclass(frozen=True, slots=True)
class DiffusionKernel:
    """Verteilungsmuster des Fehlers.

    Attributes:
        offsets: Tupel aus ``(dx, dy, gewicht)``: Nachbar rechts (dx > 0) bzw. darunter
            (dy > 0) bekommt ``gewicht / divisor`` des Fehlers.
        divisor: Nenner der Gewichte.
    """

    offsets: tuple[tuple[int, int, int], ...]
    divisor: int


# Floyd-Steinberg: 7/16 nach rechts, 3/16 links unten, 5/16 unten, 1/16 rechts unten
#         X   7
#     3   5   1
FLOYD_STEINBERG = DiffusionKernel(offsets=((1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)), divisor=16)

# Atkinson: verteilt nur 6/8 des Fehlers; der Rest geht verloren → mehr Kontrast
#         X   1   1
#     1   1   1
#         1
ATKINSON = DiffusionKernel(
    offsets=((1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)), divisor=8
)

# Sierra Lite: sehr kleiner, schneller Kernel
#         X   2
#     1   1
SIERRA_LITE = DiffusionKernel(offsets=((1, 0, 2), (-1, 1, 1), (0, 1, 1)), divisor=4)


class ErrorDiffusionDitherer:
    """Fehlerdiffusion im Lab-Raum mit beliebigem Kernel (Strategy Pattern).

    Args:
        kernel: Verteilungsmuster des Fehlers.
        color_space: Farbsuche (Farbabstand austauschbar).
        strength: Anteil des Fehlers, der weitergegeben wird (0.0 bis 1.0).
        serpentine: Ungerade Zeilen von rechts nach links abarbeiten.
    """

    def __init__(
        self, kernel: DiffusionKernel, color_space: ColorSpace, *, strength: float, serpentine: bool
    ) -> None:
        self._kernel = kernel
        self._color_space = color_space
        self._strength = strength
        self._serpentine = serpentine

    def dither(self, image_lab: FloatImage, palette_lab: FloatImage) -> IndexGrid:
        """Gibt ein H×W-Raster mit Palettenindizes zurück."""
        # copy: Die Fehler werden ins Arbeitsbild geschrieben, das Original bleibt unverändert
        work = np.array(image_lab, dtype=np.float64, copy=True)
        height, width, _ = work.shape
        result = np.empty((height, width), dtype=np.intp)
        for row in range(height):
            # Serpentine: in ungeraden Zeilen (row % 2 == 1, % = Rest der Division) rückwärts
            direction = -1 if self._serpentine and row % 2 == 1 else 1
            # range(start, stop, schritt): mit Schritt -1 läuft die Schleife rückwärts
            columns = range(width) if direction == 1 else range(width - 1, -1, -1)
            for col in columns:
                index = int(self._color_space.nearest(work[row, col], palette_lab))
                result[row, col] = index
                error = (work[row, col] - palette_lab[index]) * self._strength
                self._diffuse(work, row, col, error, direction)
        return result

    def _diffuse(
        self, work: FloatImage, row: int, col: int, error: FloatImage, direction: int
    ) -> None:
        """Verteilt den Fehler eines Pixels auf seine Nachbarn laut Kernel."""
        height, width, _ = work.shape
        for dx, dy, weight in self._kernel.offsets:
            # Rückwärts laufende Zeilen spiegeln den Kernel horizontal (dx * -1)
            target_row, target_col = row + dy, col + dx * direction
            if 0 <= target_row < height and 0 <= target_col < width:
                pixel = work[target_row, target_col]  # View: Änderungen landen direkt in work
                pixel += error * (weight / self._kernel.divisor)
                _clamp_lab(pixel)


def _clamp_lab(pixel: FloatImage) -> None:
    """Begrenzt ein Lab-Pixel in place auf gültige Bereiche."""
    # out=...: np.clip schreibt das Ergebnis zurück in dasselbe Array (kein neues Objekt)
    np.clip(pixel[0:1], *L_RANGE, out=pixel[0:1])
    np.clip(pixel[1:3], *AB_RANGE, out=pixel[1:3])
