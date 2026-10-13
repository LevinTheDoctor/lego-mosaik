"""Bild bearbeiten, bevor es zum Mosaik wird: drehen, spiegeln, Ausschnitt und Farbfilter."""

from collections.abc import Callable, Mapping

lazy from PIL import Image, ImageEnhance, ImageOps

from lego_mosaik.domain.models import ColorFilter

FULL_STRENGTH = 1.0
PERCENT = 100.0
POSTER_BITS = 3  # Bits pro Kanal beim Plakat-Look: 8 Tonstufen
# Kanalfaktoren (Rot, Grün, Blau) für warme und kalte Töne
WARM_FACTORS = (1.12, 1.03, 0.85)
COOL_FACTORS = (0.88, 1.0, 1.12)
# Sepia: Grauwerte auf einen Verlauf von Dunkelbraun über Braun nach Creme legen
SEPIA_DARK, SEPIA_MID, SEPIA_LIGHT = "#2B1D0E", "#A0784A", "#FFF1D6"
VINTAGE_DARK, VINTAGE_LIGHT = "#3A2C1E", "#F2E4C6"  # verblasst: kein echtes Schwarz/Weiß
VINTAGE_MIX = 0.6  # Anteil des Sepia-Verlaufs beim Vintage-Filter
VINTAGE_CONTRAST = 0.85
# Drehung im Uhrzeigersinn → Pillow-Transpose (Pillow dreht gegen den Uhrzeigersinn)
_ROTATIONS: Mapping[int, str] = {90: "ROTATE_270", 180: "ROTATE_180", 270: "ROTATE_90"}


def transform(
    image: Image.Image,
    *,
    rotate: int,
    mirror: bool,
    crop: tuple[float, float, float, float] | None,
) -> Image.Image:
    """Dreht (im Uhrzeigersinn), spiegelt waagerecht und schneidet einen Ausschnitt aus.

    Der Ausschnitt (x, y, Breite, Höhe in Prozent) bezieht sich auf das gedrehte und
    gespiegelte Bild, also auf das, was man nach diesen Schritten sieht.
    """
    if rotate in _ROTATIONS:
        image = image.transpose(Image.Transpose[_ROTATIONS[rotate]])
    if mirror:
        image = ImageOps.mirror(image)
    if crop is not None:
        x, y, width, height = crop
        box = (
            round(image.width * x / PERCENT),
            round(image.height * y / PERCENT),
            round(image.width * (x + width) / PERCENT),
            round(image.height * (y + height) / PERCENT),
        )
        # mindestens ein Pixel, auch bei sehr kleinen Ausschnitten
        image = image.crop((box[0], box[1], max(box[2], box[0] + 1), max(box[3], box[1] + 1)))
    return image


def apply_filter(image: Image.Image, color_filter: ColorFilter, strength: float) -> Image.Image:
    """Legt einen Farbfilter auf ein RGB-Bild; ``strength`` < 1 mischt mit dem Original."""
    if color_filter is ColorFilter.NONE or strength == 0:
        return image
    filtered = _FILTERS[color_filter](image)
    if strength >= FULL_STRENGTH:
        return filtered
    # Image.blend: (1 - strength) × Original + strength × Filter
    return Image.blend(image, filtered, strength)


def _grayscale(image: Image.Image) -> Image.Image:
    return ImageOps.grayscale(image).convert("RGB")


def _sepia(image: Image.Image) -> Image.Image:
    # colorize färbt ein Graustufenbild entlang eines Verlaufs von black über mid nach white
    return ImageOps.colorize(
        ImageOps.grayscale(image), black=SEPIA_DARK, white=SEPIA_LIGHT, mid=SEPIA_MID
    )


def _scale_channels(factors: tuple[float, float, float]) -> Callable[[Image.Image], Image.Image]:
    """Filter, der jeden Farbkanal mit einem Faktor multipliziert (begrenzt auf 255)."""

    def scaled(image: Image.Image) -> Image.Image:
        bands = [
            band.point(lambda value, f=factor: min(255, round(value * f)))
            for band, factor in zip(image.split(), factors, strict=True)
        ]
        return Image.merge("RGB", bands)

    return scaled


def _invert(image: Image.Image) -> Image.Image:
    return ImageOps.invert(image)


def _posterize(image: Image.Image) -> Image.Image:
    return ImageOps.posterize(image, POSTER_BITS)


def _vintage(image: Image.Image) -> Image.Image:
    toned = ImageOps.colorize(ImageOps.grayscale(image), black=VINTAGE_DARK, white=VINTAGE_LIGHT)
    mixed = Image.blend(image, toned, VINTAGE_MIX)
    return ImageEnhance.Contrast(mixed).enhance(VINTAGE_CONTRAST)


_FILTERS: Mapping[ColorFilter, Callable[[Image.Image], Image.Image]] = {
    ColorFilter.GRAYSCALE: _grayscale,
    ColorFilter.SEPIA: _sepia,
    ColorFilter.WARM: _scale_channels(WARM_FACTORS),
    ColorFilter.COOL: _scale_channels(COOL_FACTORS),
    ColorFilter.INVERT: _invert,
    ColorFilter.POSTERIZE: _posterize,
    ColorFilter.VINTAGE: _vintage,
}
