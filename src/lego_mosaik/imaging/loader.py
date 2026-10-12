"""Bild laden: EXIF-Drehung korrigieren, Transparenz auf eine Hintergrundfarbe legen, RGB."""

import io
import logging
from pathlib import Path

lazy from PIL import Image, ImageOps, UnidentifiedImageError

from lego_mosaik.domain.errors import InvalidImageError
from lego_mosaik.domain.models import RgbTuple

logger = logging.getLogger(__name__)

# Bildmodi mit Alphakanal bzw. Palettenbilder, die Transparenz enthalten können
ALPHA_MODES = frozenset({"RGBA", "LA", "PA", "RGBa", "La"})


def load_image(source: Path | bytes, background: RgbTuple) -> Image.Image:
    """Lädt ein Bild aus einer Datei oder aus Bytes (z. B. ein Upload) als RGB.

    Args:
        source: Dateipfad oder Dateiinhalt.
        background: Farbe, auf die transparente Bereiche gelegt werden.

    Returns:
        Ein RGB-Bild mit korrigierter Ausrichtung.

    Raises:
        InvalidImageError: Wenn die Datei fehlt oder kein lesbares Bild ist.
    """
    image = _open(source)
    # Handykameras speichern oft "hochkant" nur als EXIF-Hinweis; exif_transpose
    # dreht die Pixel tatsächlich, damit das Bild nicht auf der Seite liegt
    image = ImageOps.exif_transpose(image)
    logger.info("Bild geladen: %d × %d Pixel, Modus %s.", image.width, image.height, image.mode)
    return _flatten_alpha(image, background)


def _open(source: Path | bytes) -> Image.Image:
    """Öffnet das Bild und liest die Pixel sofort, damit Fehler hier auffallen."""
    if isinstance(source, Path) and not source.is_file():
        raise InvalidImageError(f"Die Bilddatei {source} wurde nicht gefunden.")
    try:
        # isinstance prüft den Typ zur Laufzeit; BytesIO macht aus Bytes ein dateiähnliches Objekt
        image = Image.open(io.BytesIO(source) if isinstance(source, bytes) else source)
        image.load()  # Image.open liest nur den Kopf; load() dekodiert die Pixel
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError) as error:
        raise InvalidImageError(f"Die Datei ist kein lesbares Bild ({error}).") from error
    return image


def _flatten_alpha(image: Image.Image, background: RgbTuple) -> Image.Image:
    """Legt ein Bild mit Transparenz auf eine einfarbige Fläche und gibt RGB zurück."""
    has_alpha = image.mode in ALPHA_MODES or "transparency" in image.info
    if not has_alpha:
        return image.convert("RGB")
    rgba = image.convert("RGBA")
    canvas = Image.new("RGBA", rgba.size, (*background, 255))  # (*tupel, 255): Tupel entpacken
    # alpha_composite legt rgba gemäß seinem Alphakanal über die Hintergrundfläche
    return Image.alpha_composite(canvas, rgba).convert("RGB")
