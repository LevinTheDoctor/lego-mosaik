"""Schnittstelle für die Bildaufbereitung."""

from pathlib import Path
from typing import Protocol

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.models import RgbTuple

type RgbImage = npt.NDArray[np.uint8]
"""H×W×3-Bild mit RGB-Werten 0–255."""


class ImagePreparer(Protocol):
    """Lädt ein Bild und bringt es auf die Mosaikgröße (1 Pixel = 1 Noppe)."""

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
