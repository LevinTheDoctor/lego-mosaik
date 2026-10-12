"""Zweidimensionale Pläne für die Bauanleitung (Draufsicht, nur Pillow).

- Abschnittskarte: Wo liegt welcher Abschnitt, was ist schon gebaut?
- Verbindungsplan: Wo stecken die Technic-Pins zwischen den Art-Paneelen?
- Pin-Symbol: kleines Bild eines Technic-Pins für Teile-Kästen
"""

from collections.abc import Iterable, Sequence

lazy from PIL import Image, ImageDraw

from lego_mosaik.domain.models import Placement, RgbTuple
from lego_mosaik.domain.sections import Section
from lego_mosaik.outputs.iso_render import HIGHLIGHT_RGB, edge_color
from lego_mosaik.outputs.rendering import Font, Region, render_placements

WHITE: RgbTuple = (255, 255, 255)
BLACK: RgbTuple = (0, 0, 0)
MAP_HIGHLIGHT: RgbTuple = (220, 30, 30)  # aktueller Abschnitt
MAP_HIGHLIGHT_WIDTH = 4
PLAN_PANEL_FILL: RgbTuple = (232, 232, 232)  # Paneele schematisch hellgrau
PLAN_PANEL_BORDER: RgbTuple = (90, 90, 90)
PLAN_BORDER_WIDTH = 2
PIN_THICKNESS_RATIO = 0.5  # Dicke eines Pins im Plan als Anteil einer Noppe
PIN_ICON_RATIO = 0.42  # Höhe zu Breite des Pin-Symbols
PIN_COLLAR_RATIO = 0.12  # Breite des Mittelrings als Anteil der Pinlänge


def map_stud_px(width: int, height: int, max_px: int, max_stud_px: int) -> int:
    """Noppengröße, damit ``width`` × ``height`` Noppen in ``max_px`` passen (mind. 1 px)."""
    return max(1, min(max_stud_px, max_px // max(width, height)))


def section_map(
    placements: Iterable[Placement],
    sections: Sequence[Section],
    size: tuple[int, int],
    *,
    stud_px: int,
    font: Font,
    background: RgbTuple,
    current: Section | None = None,
) -> Image.Image:
    """Draufsicht mit Abschnittsgrenzen und -nummern.

    Args:
        placements: Bereits gebaute Teile; der Rest erscheint in ``background``.
        sections: Alle Abschnitte des Bilds.
        size: (Breite, Höhe) des Mosaiks in Noppen.
        stud_px: Kantenlänge einer Noppe in Pixeln.
        font: Schrift für die Nummern.
        background: Farbe noch nicht gebauter Zellen (z. B. Farbe des Unterbaus).
        current: Abschnitt, der rot umrandet wird (der als Nächstes gebaut wird).
    """
    width, height = size
    image = render_placements(
        placements,
        region=Region(0, 0, height, width),
        stud_px=stud_px,
        draw_part_edges=False,
        background=background,
    )
    draw = ImageDraw.Draw(image)
    for section in sections:
        box = _box(section.row, section.col, section.rows, section.cols, stud_px)
        draw.rectangle(box, outline=WHITE, width=1)
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        # stroke_*: weiße Schrift mit schwarzem Rand ist auf jeder Farbe lesbar
        draw.text(
            center,
            str(section.number),
            fill=WHITE,
            font=font,
            anchor="mm",
            stroke_width=2,
            stroke_fill=BLACK,
        )
    if current is not None:
        box = _box(current.row, current.col, current.rows, current.cols, stud_px)
        draw.rectangle(box, outline=MAP_HIGHLIGHT, width=MAP_HIGHLIGHT_WIDTH)
    return image


def connection_plan(
    substrate: Sequence[Placement],
    connectors: Iterable[Placement],
    *,
    stud_px: int,
    font: Font,
) -> Image.Image:
    """Schema des Unterbaus: Paneele als nummerierte Felder, Pins als orange Balken.

    Ein Pin steckt zur Hälfte in jedem der beiden Paneele, deshalb liegt sein
    Balken quer über der Naht.
    """
    width = max(plate.col + plate.cols_spanned for plate in substrate)
    height = max(plate.row + plate.rows_spanned for plate in substrate)
    image = Image.new("RGB", (width * stud_px, height * stud_px), WHITE)
    draw = ImageDraw.Draw(image)
    for number, plate in enumerate(substrate, start=1):
        box = _box(plate.row, plate.col, plate.rows_spanned, plate.cols_spanned, stud_px)
        draw.rectangle(
            box, fill=PLAN_PANEL_FILL, outline=PLAN_PANEL_BORDER, width=PLAN_BORDER_WIDTH
        )
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        draw.text(center, str(number), fill=PLAN_PANEL_BORDER, font=font, anchor="mm")
    for pin in connectors:
        draw.rectangle(_pin_box(pin, stud_px), fill=HIGHLIGHT_RGB, outline=BLACK)
    return image


def pin_icon(rgb: RgbTuple, background: RgbTuple, width_px: int) -> Image.Image:
    """Technic-Pin von der Seite: abgerundeter Zylinder mit Ring in der Mitte."""
    height_px = round(width_px * PIN_ICON_RATIO)
    image = Image.new("RGB", (width_px, height_px), background)
    draw = ImageDraw.Draw(image)
    outline = edge_color(rgb)
    radius = height_px // 2
    draw.rounded_rectangle(
        (0, 0, width_px - 1, height_px - 1), radius=radius, fill=rgb, outline=outline, width=2
    )
    collar = round(width_px * PIN_COLLAR_RATIO / 2)
    middle = width_px // 2
    draw.rectangle((middle - collar, 0, middle + collar, height_px - 1), outline=outline, width=2)
    return image


def _box(row: int, col: int, rows: int, cols: int, stud_px: int) -> tuple[int, int, int, int]:
    """Pixel-Rechteck (links, oben, rechts, unten) eines Bereichs in Noppen."""
    return col * stud_px, row * stud_px, (col + cols) * stud_px - 1, (row + rows) * stud_px - 1


def _pin_box(pin: Placement, stud_px: int) -> tuple[float, float, float, float]:
    """Schmaler Balken über den beiden Zellen, die der Pin überspannt."""
    left, top, right, bottom = _box(pin.row, pin.col, pin.rows_spanned, pin.cols_spanned, stud_px)
    inset = stud_px * (1 - PIN_THICKNESS_RATIO) / 2  # Abstand zum Rand quer zum Pin
    if pin.rotated:  # senkrechter Pin über einer waagerechten Naht
        return left + inset, top, right - inset, bottom
    return left, top + inset, right, bottom - inset
