"""Bild auf die Mosaikgröße bringen: Kontrast/Sättigung, zuschneiden oder einpassen, skalieren."""

import logging
from collections.abc import Mapping

lazy from PIL import Image, ImageEnhance, ImageOps

from lego_mosaik.domain.models import FitMode, Resampling, RgbTuple

logger = logging.getLogger(__name__)

NEUTRAL_FACTOR = 1.0  # Faktor 1.0 lässt ein Bild bei ImageEnhance unverändert
CENTER = (0.5, 0.5)  # Ausschnitt bzw. Bild mittig platzieren (x, y als Anteil)

# Unser Enum → Pillow-Name. Die Zuordnung steht hier, damit die Domain Pillow nicht kennt.
_PIL_RESAMPLING_NAMES: Mapping[Resampling, str] = {
    Resampling.BOX: "BOX",
    Resampling.BILINEAR: "BILINEAR",
    Resampling.LANCZOS: "LANCZOS",
    Resampling.NEAREST: "NEAREST",
}


def enhance(image: Image.Image, *, contrast: float, saturation: float) -> Image.Image:
    """Passt Kontrast und Sättigung an. Bei Faktor 1.0 bleibt das Bild unverändert.

    Läuft *vor* dem Einpassen, damit der Rand bei ``PAD`` seine exakte Farbe behält.
    """
    if contrast != NEUTRAL_FACTOR:
        image = ImageEnhance.Contrast(image).enhance(contrast)
    if saturation != NEUTRAL_FACTOR:
        # ImageEnhance.Color steuert die Sättigung (0.0 = Graustufen)
        image = ImageEnhance.Color(image).enhance(saturation)
    return image


def fit_image(
    image: Image.Image,
    *,
    width: int,
    height: int,
    fit_mode: FitMode,
    pad_color: RgbTuple,
    resampling: Resampling,
) -> Image.Image:
    """Bringt das Bild auf genau ``width`` × ``height`` Pixel (1 Pixel = 1 Noppe).

    Args:
        image: RGB-Bild.
        width: Zielbreite in Noppen.
        height: Zielhöhe in Noppen.
        fit_mode: ``CROP`` schneidet mittig auf das Seitenverhältnis zu,
            ``PAD`` passt das ganze Bild ein und füllt den Rest mit ``pad_color``.
        pad_color: Randfarbe bei ``PAD``.
        resampling: Skalierverfahren.
    """
    method = Image.Resampling[_PIL_RESAMPLING_NAMES[resampling]]
    size = (width, height)
    if fit_mode is FitMode.CROP:
        # ImageOps.fit: größtmöglichen mittigen Ausschnitt im Zielverhältnis wählen, dann skalieren
        fitted = ImageOps.fit(image, size, method=method, centering=CENTER)
    else:
        # ImageOps.pad: ganzes Bild einpassen, freie Fläche mit der Randfarbe auffüllen
        fitted = ImageOps.pad(image, size, method=method, color=pad_color, centering=CENTER)
    logger.info("Bild auf %d × %d Noppen gebracht (%s).", width, height, fit_mode.value)
    return fitted
