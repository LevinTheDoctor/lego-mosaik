"""Hintergrund eines Bilds erkennen und anpassen (offline, ohne KI-Modell).

Verfahren: Der Hintergrund ist die häufigste Farbe am Bildrand. Dazu gehört jedes Pixel,
das dieser Farbe ähnlich ist (ΔE unter der Toleranz) und zusammenhängend vom Rand aus
erreichbar ist. Gleichfarbige Flächen mitten im Motiv bleiben so Teil des Motivs (wie
beim Relief „motiv“). Gut für Produktfotos, Logos, Porträts vor ruhigem Hintergrund;
bei unruhigen Hintergründen hilft eine höhere Toleranz.
"""

import io
from collections.abc import Callable, Mapping
from dataclasses import dataclass

lazy import numpy as np
lazy from PIL import Image, ImageEnhance, ImageFilter
lazy from skimage.measure import label
lazy from skimage.morphology import remove_small_holes

from lego_mosaik.domain.models import BackgroundMode, BoolGrid, RgbTuple
from lego_mosaik.imaging.color_space import rgb_to_lab

WORK_SIZE = 512  # Erkennung auf höchstens so großem Bild (schnell, Ergebnis wird hochskaliert)
LAB_BIN = 8.0  # Randfarben in Lab-Würfel dieser Kantenlänge einteilen, um die häufigste zu finden
HOLE_SHARE = 0.002  # Lücken im Hintergrund bis zu diesem Anteil der Fläche werden geschlossen
MASK_THRESHOLD = 128  # Graustufen-Maske nach dem Skalieren: ab hier Hintergrund
WHITE, BLACK = (255, 255, 255), (0, 0, 0)
MAX_BLUR_SHARE = 0.03  # Weichzeichnen bei Stärke 1: Radius 3 % der längeren Bildseite


@dataclass(frozen=True, slots=True)
class BackgroundInfo:
    """Erkannter Hintergrund.

    Attributes:
        mask: H×W-Maske in Bildgröße, True = Hintergrund.
        color: Mittlere Farbe des Hintergrunds am Rand.
        share: Anteil des Hintergrunds an der Bildfläche (0 bis 1).
    """

    mask: BoolGrid
    color: RgbTuple
    share: float


def detect_background(image: Image.Image, tolerance: float) -> BackgroundInfo:
    """Erkennt den Hintergrund eines RGB-Bilds (siehe Moduldoku)."""
    small = image.copy()
    small.thumbnail((WORK_SIZE, WORK_SIZE), Image.Resampling.BOX)  # verkleinert, behält Format
    rgb = np.asarray(small, dtype=np.float64)
    lab = rgb_to_lab(rgb)
    on_border = np.zeros(lab.shape[:2], dtype=bool)
    on_border[[0, -1], :] = True
    on_border[:, [0, -1]] = True
    # Häufigsten Farbwürfel am Rand finden, dann dessen Pixel mitteln
    bins = np.floor(lab[on_border] / LAB_BIN).astype(np.int64)
    values, counts = np.unique(bins, axis=0, return_counts=True)
    in_bin = (bins == values[counts.argmax()]).all(axis=1)
    background_lab = lab[on_border][in_bin].mean(axis=0)
    red, green, blue = (round(v) for v in rgb[on_border][in_bin].mean(axis=0).tolist())
    # CIE76: euklidischer Abstand im Lab-Raum (schnell, für die Maske genau genug)
    similar = np.linalg.norm(lab - background_lab, axis=-1) <= tolerance
    # label: zusammenhängende Flächen (4er-Nachbarschaft) durchnummerieren, 0 = nicht ähnlich
    regions = label(similar, connectivity=1)
    touching = np.unique(regions[on_border & similar])
    mask = np.isin(regions, touching[touching > 0])
    # kleine Lücken (Rauschen, Schatten) im Hintergrund schließen
    mask = remove_small_holes(mask, max_size=max(1, int(mask.size * HOLE_SHARE)))
    full = _resize_mask(mask, image.size)
    return BackgroundInfo(full, (red, green, blue), float(full.mean()))


def adjust_background(
    image: Image.Image,
    mask: BoolGrid,
    mode: BackgroundMode,
    *,
    amount: float,
    color: RgbTuple,
) -> Image.Image:
    """Verändert nur den Hintergrund (``mask``) eines RGB-Bilds.

    Args:
        image: Das Bild.
        mask: True = Hintergrund (in Bildgröße).
        mode: Was mit dem Hintergrund passiert.
        amount: Stärke 0–1 (nicht für ``REMOVE``).
        color: Neue Farbe bei ``REMOVE``.
    """
    if mode is BackgroundMode.KEEP or not mask.any():
        return image
    changed = _ADJUSTMENTS[mode](image, amount, color)
    # composite: aus ``changed``, wo die Maske weiß ist, sonst aus ``image``
    return Image.composite(changed, image, mask_image(mask))


def mask_image(mask: BoolGrid) -> Image.Image:
    """Maske als Graustufenbild (weiß = Hintergrund)."""
    return Image.fromarray(np.where(mask, 255, 0).astype(np.uint8), mode="L")


def mask_png(mask: BoolGrid, max_px: int) -> bytes:
    """Maske als PNG (weiß = Hintergrund), höchstens ``max_px`` an der längeren Seite."""
    image = mask_image(mask)
    image.thumbnail((max_px, max_px), Image.Resampling.BOX)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _resize_mask(mask: BoolGrid, size: tuple[int, int]) -> BoolGrid:
    """Skaliert eine Maske weich auf ``size`` (Breite, Höhe) und schneidet bei 50 %."""
    scaled = mask_image(mask).resize(size, Image.Resampling.BILINEAR)
    return np.asarray(scaled) >= MASK_THRESHOLD


def _solid(image: Image.Image, amount: float, color: RgbTuple) -> Image.Image:
    return Image.new("RGB", image.size, color)


def _towards(target: RgbTuple) -> Callable[[Image.Image, float, RgbTuple], Image.Image]:
    """Mischt das Bild mit einer Farbe (aufhellen: Weiß, abdunkeln: Schwarz)."""

    def blended(image: Image.Image, amount: float, color: RgbTuple) -> Image.Image:
        return Image.blend(image, Image.new("RGB", image.size, target), amount)

    return blended


def _desaturate(image: Image.Image, amount: float, color: RgbTuple) -> Image.Image:
    return ImageEnhance.Color(image).enhance(1.0 - amount)


def _blur(image: Image.Image, amount: float, color: RgbTuple) -> Image.Image:
    radius = amount * MAX_BLUR_SHARE * max(image.size)
    return image.filter(ImageFilter.GaussianBlur(radius))


_ADJUSTMENTS: Mapping[BackgroundMode, Callable[[Image.Image, float, RgbTuple], Image.Image]] = {
    BackgroundMode.REMOVE: _solid,
    BackgroundMode.LIGHTEN: _towards(WHITE),
    BackgroundMode.DARKEN: _towards(BLACK),
    BackgroundMode.DESATURATE: _desaturate,
    BackgroundMode.BLUR: _blur,
}
