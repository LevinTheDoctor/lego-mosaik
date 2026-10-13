"""Bildaufbereitung mit Pillow: laden, bearbeiten, Hintergrund, einpassen."""

import io
from dataclasses import dataclass
from pathlib import Path

lazy import numpy as np
lazy from PIL import Image

from lego_mosaik.domain.models import (
    BackgroundMode,
    BoolGrid,
    ColorFilter,
    FitMode,
    Resampling,
    RgbTuple,
)
from lego_mosaik.imaging.background import (
    adjust_background,
    detect_background,
    mask_image,
    mask_png,
)
from lego_mosaik.imaging.filters import apply_filter, transform
from lego_mosaik.imaging.fitting import enhance, fit_image, fit_mask
from lego_mosaik.imaging.loader import inspect_image, load_image
from lego_mosaik.imaging.protocol import BackgroundReport, ImageInfo, PreparedImage

MASK_THRESHOLD = 128  # eingepasste Maske: ab halber Fläche gilt eine Noppe als Hintergrund


@dataclass(frozen=True, slots=True)
class ImageEdits:
    """Bearbeitung vor dem Einpassen (Werte aus ``MosaicConfig``)."""

    contrast: float = 1.0
    saturation: float = 1.0
    brightness: float = 1.0
    color_filter: ColorFilter = ColorFilter.NONE
    filter_strength: float = 1.0
    rotate: int = 0
    mirror: bool = False
    crop: tuple[float, float, float, float] | None = None
    background: BackgroundMode = BackgroundMode.KEEP
    background_tolerance: float = 12.0
    background_amount: float = 0.5


class PillowImagePreparer:
    """Erfüllt das Protocol ``ImagePreparer`` (Facade über loader, filters, background, fitting).

    Facade Pattern: Eine einfache Methode ``prepare`` verbirgt die einzelnen
    Schritte Laden, Bearbeiten, Hintergrund und Einpassen vor der Pipeline.

    Args:
        fit_mode: Zuschneiden oder Einpassen.
        resampling: Skalierverfahren.
        edits: Drehen, Ausschnitt, Filter, Helligkeit/Kontrast/Sättigung, Hintergrund.
    """

    def __init__(self, *, fit_mode: FitMode, resampling: Resampling, edits: ImageEdits) -> None:
        self._fit_mode = fit_mode
        self._resampling = resampling
        self._edits = edits

    def inspect(self, source: Path | bytes) -> ImageInfo:
        """Größe und Format des Bilds (z. B. für eine Seite aus dem Seitenverhältnis).

        Drehung und Ausschnitt sind eingerechnet: Das Seitenverhältnis passt zum Mosaik.
        """
        info = inspect_image(source)
        edits = self._edits
        if edits.rotate == 0 and edits.crop is None:
            return info
        size = transform(
            Image.new("1", (info.width, info.height)),
            rotate=edits.rotate,
            mirror=False,
            crop=edits.crop,
        ).size
        return ImageInfo(size[0], size[1], info.format)

    def prepare(
        self,
        source: Path | bytes,
        *,
        width: int,
        height: int,
        pad_color: RgbTuple,
        background_color: RgbTuple | None = None,
    ) -> PreparedImage:
        """Lädt, bearbeitet und passt das Bild ein; Ergebnis als NumPy-Array plus Maske."""
        image, mask = self._edited(source, pad_color, background_color or pad_color)
        fitted = fit_image(
            image,
            width=width,
            height=height,
            fit_mode=self._fit_mode,
            pad_color=pad_color,
            resampling=self._resampling,
        )
        background = None
        if mask is not None:
            small = fit_mask(mask_image(mask), width=width, height=height, fit_mode=self._fit_mode)
            background = np.asarray(small) >= MASK_THRESHOLD
        # np.asarray macht aus dem Pillow-Bild ein (H, W, 3)-Array mit uint8-Werten
        return PreparedImage(np.asarray(fitted, dtype=np.uint8), background)

    def edited(
        self, source: Path | bytes, *, max_px: int, pad_color: RgbTuple, background_color: RgbTuple
    ) -> bytes:
        """Das bearbeitete Bild als PNG (Vorschau vor dem Mosaik)."""
        image, _ = self._edited(source, pad_color, background_color)
        image.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def detect_background(
        self, source: Path | bytes, *, pad_color: RgbTuple, max_px: int
    ) -> BackgroundReport:
        """Hintergrund des gedrehten und zugeschnittenen Bilds, Maske als PNG."""
        image = self._transformed(source, pad_color)
        info = detect_background(image, self._edits.background_tolerance)
        return BackgroundReport(info.color, info.share, mask_png(info.mask, max_px))

    def _transformed(self, source: Path | bytes, pad_color: RgbTuple) -> Image.Image:
        edits = self._edits
        return transform(
            load_image(source, pad_color), rotate=edits.rotate, mirror=edits.mirror, crop=edits.crop
        )

    def _edited(
        self, source: Path | bytes, pad_color: RgbTuple, background_color: RgbTuple
    ) -> tuple[Image.Image, BoolGrid | None]:
        """Bearbeitetes Bild in voller Größe und ggf. die Hintergrund-Maske.

        Der Hintergrund wird auf dem unveränderten Bild erkannt (Filter wie Poster würden
        die Erkennung stören) und erst nach den Filtern angepasst. So behält ein entfernter
        Hintergrund genau seine LEGO-Farbe.
        """
        edits = self._edits
        image = self._transformed(source, pad_color)
        mask = None
        if edits.background is not BackgroundMode.KEEP:
            mask = detect_background(image, edits.background_tolerance).mask
        image = apply_filter(image, edits.color_filter, edits.filter_strength)
        image = enhance(
            image,
            contrast=edits.contrast,
            saturation=edits.saturation,
            brightness=edits.brightness,
        )
        if mask is not None:
            image = adjust_background(
                image,
                mask,
                edits.background,
                amount=edits.background_amount,
                color=background_color,
            )
        return image, mask
