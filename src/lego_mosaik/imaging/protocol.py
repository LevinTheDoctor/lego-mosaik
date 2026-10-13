"""Schnittstelle für die Bildaufbereitung."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.models import BoolGrid, RgbTuple

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


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Bild in Mosaikgröße und (falls ein Hintergrund-Modus gewählt ist) dessen Hintergrund.

    Attributes:
        rgb: H×W×3-Bild, 1 Pixel = 1 Noppe (bzw. Teilpixel).
        background: H×W-Maske, True = Hintergrund; ``None`` ohne Hintergrund-Modus.
    """

    rgb: RgbImage
    background: BoolGrid | None = None


@dataclass(frozen=True, slots=True)
class BackgroundReport:
    """Ergebnis der Hintergrund-Erkennung für Oberflächen.

    Attributes:
        color: Mittlere Farbe des Hintergrunds am Rand.
        share: Anteil an der Bildfläche (0 bis 1).
        mask_png: Maske als PNG (weiß = Hintergrund), verkleinert.
    """

    color: RgbTuple
    share: float
    mask_png: bytes


class ImagePreparer(Protocol):
    """Lädt ein Bild und bringt es auf die Mosaikgröße (1 Pixel = 1 Noppe)."""

    def inspect(self, source: Path | bytes) -> ImageInfo:
        """Prüft, dass ``source`` ein lesbares Bild ist, und liefert seine Eckdaten."""
        ...

    def prepare(
        self,
        source: Path | bytes,
        *,
        width: int,
        height: int,
        pad_color: RgbTuple,
        background_color: RgbTuple | None = None,
    ) -> PreparedImage:
        """Bearbeitet das Bild und bringt es auf ``height`` × ``width`` Pixel.

        Args:
            source: Dateipfad oder Dateiinhalt.
            width: Zielbreite in Noppen.
            height: Zielhöhe in Noppen.
            pad_color: Farbe für Transparenz und den Rand beim Einpassen.
            background_color: Neue Farbe beim Entfernen des Hintergrunds (sonst ``pad_color``).
        """
        ...

    def edited(
        self, source: Path | bytes, *, max_px: int, pad_color: RgbTuple, background_color: RgbTuple
    ) -> bytes:
        """Das bearbeitete Bild (ohne Einpassen) als PNG, höchstens ``max_px`` groß."""
        ...

    def detect_background(
        self, source: Path | bytes, *, pad_color: RgbTuple, max_px: int
    ) -> BackgroundReport:
        """Erkennt den Hintergrund des (gedrehten, zugeschnittenen) Bilds."""
        ...
