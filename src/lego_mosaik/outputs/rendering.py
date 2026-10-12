"""Gemeinsames Zeichnen von Mosaiken (Vorschau und später die Bauanleitung)."""

from collections.abc import Iterable

lazy from PIL import Image, ImageDraw

from lego_mosaik.domain.models import Placement, RgbTuple

# Hintergrund für nicht belegte Zellen (sollte im fertigen Mosaik nicht vorkommen)
EMPTY_CELL_RGB: RgbTuple = (128, 128, 128)
EDGE_RGB: RgbTuple = (40, 40, 40)
# Abstand des Noppenkreises vom Zellrand als Anteil der Zellgröße
STUD_INSET_RATIO = 0.2
# Helligkeit (0–255), ab der eine Farbe als "hell" gilt und einen dunkleren Noppenrand bekommt
LIGHT_THRESHOLD = 128
SHADE_FACTOR_DARKER = 0.75
SHADE_FACTOR_LIGHTER = 1.35
MIN_HIGHLIGHT = 48  # Mindesthelligkeit des Noppenkreises auf sehr dunklen Farben
# Gewichte für die wahrgenommene Helligkeit (ITU-R BT.601): Grün wirkt am hellsten
LUMA_WEIGHTS = (0.299, 0.587, 0.114)
RGB_MAX = 255


def render_placements(
    placements: Iterable[Placement],
    *,
    width: int,
    height: int,
    stud_px: int,
    draw_part_edges: bool,
) -> Image.Image:
    """Zeichnet Teile als Noppen-Quadrate mit angedeutetem Noppenkreis.

    Args:
        placements: Die zu zeichnenden Teile.
        width: Breite des Rasters in Noppen.
        height: Höhe des Rasters in Noppen.
        stud_px: Kantenlänge einer Noppe in Pixeln.
        draw_part_edges: Umrisse der Teile als Linien einzeichnen.
    """
    image = Image.new("RGB", (width * stud_px, height * stud_px), EMPTY_CELL_RGB)
    draw = ImageDraw.Draw(image)
    placements = list(placements)  # wird zweimal durchlaufen, daher in eine Liste
    for placement in placements:
        for row, col in placement.cells():
            _draw_stud(draw, row, col, stud_px, placement.color.rgb)
    if draw_part_edges:
        for placement in placements:
            draw.rectangle(part_box(placement, stud_px), outline=EDGE_RGB)
    return image


def part_box(placement: Placement, stud_px: int) -> tuple[int, int, int, int]:
    """Pixel-Rechteck (links, oben, rechts, unten) eines Teils, inklusive Randpixel."""
    left, top = placement.col * stud_px, placement.row * stud_px
    right = left + placement.cols_spanned * stud_px - 1
    bottom = top + placement.rows_spanned * stud_px - 1
    return left, top, right, bottom


def _draw_stud(draw: ImageDraw.ImageDraw, row: int, col: int, stud_px: int, rgb: RgbTuple) -> None:
    """Eine Zelle: gefülltes Quadrat plus Kreis in leicht abgewandelter Farbe."""
    left, top = col * stud_px, row * stud_px
    draw.rectangle((left, top, left + stud_px - 1, top + stud_px - 1), fill=rgb)
    inset = round(stud_px * STUD_INSET_RATIO)
    circle = (left + inset, top + inset, left + stud_px - 1 - inset, top + stud_px - 1 - inset)
    draw.ellipse(circle, outline=shade(rgb), width=max(1, stud_px // 16))


def shade(rgb: RgbTuple) -> RgbTuple:
    """Hellere Farben werden abgedunkelt, dunkle aufgehellt, damit der Noppenkreis sichtbar ist."""
    # zip paart Kanal und Gewicht: (R, 0.299), (G, 0.587), (B, 0.114)
    luma = sum(channel * weight for channel, weight in zip(rgb, LUMA_WEIGHTS, strict=True))
    if luma >= LIGHT_THRESHOLD:
        return _scale(rgb, SHADE_FACTOR_DARKER)
    # Reines Schwarz bliebe beim Multiplizieren schwarz, daher eine Mindesthelligkeit
    red, green, blue = _scale(rgb, SHADE_FACTOR_LIGHTER)
    return max(red, MIN_HIGHLIGHT), max(green, MIN_HIGHLIGHT), max(blue, MIN_HIGHLIGHT)


def _scale(rgb: RgbTuple, factor: float) -> RgbTuple:
    """Multipliziert alle Kanäle mit ``factor`` und begrenzt auf 0–255."""
    red, green, blue = (min(RGB_MAX, round(channel * factor)) for channel in rgb)
    return red, green, blue
