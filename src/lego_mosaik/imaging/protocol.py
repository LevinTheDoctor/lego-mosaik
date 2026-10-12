"""Schnittstelle für die Bildaufbereitung."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.models import RgbTuple

type RgbImage = npt.NDArray[np.uint8]
"""H×W×3-Bild mit RGB-Werten 0–255."""


@dataclass(frozen=True, slots=True)
class ImageInfo:
    """Eckdaten eines Bilds, so wie es nach der EXIF-Drehung aussieht.

    Attributes:
        width: Breite in Pixeln.
        height: Höhe in Pixeln.
        format: Dateiformat laut Pillow, z. B. "JPEG", oder ``None``.
    """

    width: int
    height: int
    format: str | None

    @property
    def aspect_ratio(self) -> float:
        """Seitenverhältnis Breite / Höhe (> 1 = Querformat)."""
        return self.width / self.height


class ImagePreparer(Protocol):
    """Lädt ein Bild und bringt es auf die Mosaikgröße (1 Pixel = 1 Noppe)."""

    def inspect(self, source: Path | bytes) -> ImageInfo:
        """Prüft, dass ``source`` ein lesbares Bild ist, und liefert seine Eckdaten."""
        ...

    def prepare(
        self, source: Path | bytes, *, width: int, height: int, pad_color: RgbTuple
    ) -> RgbImage:
        """Gibt ein ``height`` × ``width`` × 3-Array zurück.

        Args:
            source: Dateipfad oder Dateiinhalt.
            width: Zielbreite in Noppen.
            height: Zielhöhe in Noppen.
            pad_color: Farbe für Transparenz und den Rand beim Einpassen.
        """
        ...
