"""Einfacher 3D-Renderer für flache Mosaike (nur Pillow, keine externe Software).

Projektion: orthografisch von schräg vorne oben. Die Welt wird zuerst um die
senkrechte Achse gedreht (Azimut), dann von oben unter einem Höhenwinkel
betrachtet (Elevation). Für flache Mosaike eignet sich ein gerader Blick (Azimut 0°)
mit steiler Elevation (ca. 60°) wie in LEGO-Art-Anleitungen: Zeilen und Spalten
bleiben gerade und abzählbar, Noppen und Vorderkanten wirken trotzdem plastisch.

Verdeckung: Painter's Algorithm. Jede Zelle wird als kleine Säule gezeichnet,
von hinten nach vorne. Was näher ist, wird später gemalt und überdeckt damit
korrekt, was dahinter liegt. Weil alle Teile gleich hoch sind und in einer Ebene
liegen, reicht das Sortieren nach der Tiefe des Zellmittelpunkts.

Koordinaten wie in LDraw (1 Noppe = 20 LDU), aber mit y nach **oben**:
x = Spalten nach rechts, z = Zeilen nach vorne (zum Betrachter), y = Höhe.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

lazy from PIL import Image, ImageDraw

from lego_mosaik.domain.models import RgbTuple

LDU_PER_STUD = 20
PLATE_HEIGHT = 8
STUD_RADIUS = 6
STUD_HEIGHT = 4
BASEPLATE_THICKNESS = 3
BACKGROUND: RgbTuple = (255, 255, 255)
# Helligkeit der Flächen relativ zur Grundfarbe (einfache Beleuchtung von oben links)
TOP_SHADE, FRONT_SHADE, SIDE_SHADE = 1.0, 0.78, 0.62
STUD_TOP_SHADE, STUD_SIDE_SHADE = 1.1, 0.82
# Neue Teile eines Schritts bekommen eine kräftige Umrandung; die Farben selbst
# bleiben echt (ausgeblasste Farben sähen aus wie andere LEGO-Farben)
HIGHLIGHT_RGB: RgbTuple = (255, 120, 0)
HIGHLIGHT_WIDTH_FACTOR = 1.6  # Linienbreite in LDU
EDGE_DARKEN, EDGE_LIGHTEN_DARK = 0.5, 70  # Kantenfarbe: abdunkeln bzw. bei Schwarz aufhellen
DARK_LIMIT = 60  # Helligkeit, unter der Kanten aufgehellt statt abgedunkelt werden
PADDING_PX = 6
MIN_VISIBLE_AZIMUTH_DEG = 0.5  # darunter sind Seitenflächen nur noch Striche
RGB_MAX = 255

type Cell = tuple[int, int]
type Point2 = tuple[float, float]
type Point3 = tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class RenderCell:
    """Was in einer Zelle steht.

    Attributes:
        rgb: Farbe des Teils.
        part_id: Kennung des Teils; benachbarte Zellen mit gleicher Kennung gehören
            zum selben Teil (dort werden keine Kanten gezeichnet).
        has_stud: True bei Plates und Bricks, False bei Tiles (glatt).
        highlighted: True bei neuen Teilen des aktuellen Schritts (orange Umrandung).
    """

    rgb: RgbTuple
    part_id: int
    has_stud: bool = True
    highlighted: bool = False


@dataclass(frozen=True, slots=True)
class IsoView:
    """Kamerawinkel in Grad."""

    elevation_deg: float = 60.0
    azimuth_deg: float = 0.0

    def project(self, x: float, y: float, z: float) -> Point2:
        """Weltpunkt → Bildpunkt (Einheiten LDU, Bild-y zeigt nach unten)."""
        azimuth, elevation = math.radians(self.azimuth_deg), math.radians(self.elevation_deg)
        # Drehung um die senkrechte y-Achse
        rotated_x = x * math.cos(azimuth) - z * math.sin(azimuth)
        rotated_z = x * math.sin(azimuth) + z * math.cos(azimuth)
        # Blick von oben: Tiefe staucht sich mit sin(Elevation), Höhe mit cos(Elevation)
        return rotated_x, rotated_z * math.sin(elevation) - y * math.cos(elevation)

    def depth(self, x: float, z: float) -> float:
        """Je größer, desto näher an der Kamera (für die Zeichenreihenfolge)."""
        azimuth = math.radians(self.azimuth_deg)
        return x * math.sin(azimuth) + z * math.cos(azimuth)

    @property
    def shows_side_faces(self) -> bool:
        """Rechte Seitenflächen sind nur bei gedrehter Kamera sichtbar."""
        return self.azimuth_deg > MIN_VISIBLE_AZIMUTH_DEG

    @property
    def stud_squash(self) -> float:
        """Kreise auf waagerechten Flächen werden zu Ellipsen mit diesem Höhenfaktor."""
        return math.sin(math.radians(self.elevation_deg))


@dataclass(frozen=True, slots=True)
class Area:
    """Gerenderter Bereich in Noppen (oben links und Größe)."""

    row: int
    col: int
    rows: int
    cols: int

    def contains(self, row: int, col: int) -> bool:
        """True, wenn die Zelle im Bereich liegt (verkettete Vergleiche)."""
        return self.row <= row < self.row + self.rows and self.col <= col < self.col + self.cols


def render_iso(
    cells: Mapping[Cell, RenderCell],
    area: Area,
    view: IsoView,
    *,
    baseplate_rgb: RgbTuple | None,
    max_width: int,
    max_height: int,
    supersample: int = 2,
    scale: float | None = None,
    background: RgbTuple = BACKGROUND,
) -> Image.Image:
    """Rendert die Zellen eines Bereichs als 3D-Bild.

    Args:
        cells: Belegte Zellen (Zeile, Spalte) → Inhalt; fehlende Zellen sind leer.
        area: Der zu zeichnende Bereich.
        view: Kamerawinkel.
        baseplate_rgb: Farbe der Grundplatte unter dem Bereich oder ``None``.
        max_width: Höchstbreite des Bilds in Pixeln.
        max_height: Höchsthöhe des Bilds in Pixeln.
        supersample: Intern so viel größer zeichnen und dann verkleinern (Kantenglättung).
        scale: Feste Pixel pro LDU (z. B. für gleich große Teile-Icons) statt Einpassen.
        background: Hintergrundfarbe des Bilds.
    """
    canvas = _Canvas.create(area, view, max_width, max_height, supersample, scale, background)
    if baseplate_rgb is not None:
        canvas.draw_baseplate(baseplate_rgb)
    ordered = sorted(
        (
            (row, col)
            for row in range(area.row, area.row + area.rows)
            for col in range(area.col, area.col + area.cols)
        ),
        key=lambda cell: (view.depth(cell[1] + 0.5, cell[0] + 0.5), cell),
    )
    for cell in ordered:
        content = cells.get(cell)
        if content is not None:
            canvas.draw_cell(cell, content, cells)
        elif baseplate_rgb is not None:
            canvas.draw_stud(cell, 0.0, baseplate_rgb)
    # Zweiter Durchgang: Umrandungen obenauf, damit keine Nachbarzelle sie übermalt
    for cell in ordered:
        content = cells.get(cell)
        if content is not None and content.highlighted:
            canvas.draw_highlight(cell, content, cells)
    return canvas.finish()


@dataclass(slots=True)
class _Canvas:
    """Zeichenfläche mit Projektion von LDU auf Pixel (intern vergrößert)."""

    image: Image.Image
    draw: ImageDraw.ImageDraw
    view: IsoView
    area: Area
    scale: float  # Pixel pro LDU auf der vergrößerten Fläche
    offset: Point2
    supersample: int

    @classmethod
    def create(
        cls,
        area: Area,
        view: IsoView,
        max_width: int,
        max_height: int,
        supersample: int,
        scale: float | None,
        background: RgbTuple,
    ) -> _Canvas:
        """Berechnet Maßstab und Bildgröße aus den projizierten Ecken des Bereichs."""
        corners = [
            view.project(x, y, z)
            for x in (area.col * LDU_PER_STUD, (area.col + area.cols) * LDU_PER_STUD)
            for z in (area.row * LDU_PER_STUD, (area.row + area.rows) * LDU_PER_STUD)
            for y in (-BASEPLATE_THICKNESS, PLATE_HEIGHT + STUD_HEIGHT)
        ]
        xs, ys = [p[0] for p in corners], [p[1] for p in corners]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        fit = min((max_width - 2 * PADDING_PX) / span_x, (max_height - 2 * PADDING_PX) / span_y)
        pixels_per_ldu = (scale or fit) * supersample
        size = (
            math.ceil(span_x * pixels_per_ldu + 2 * PADDING_PX * supersample),
            math.ceil(span_y * pixels_per_ldu + 2 * PADDING_PX * supersample),
        )
        image = Image.new("RGB", size, background)
        offset = (
            PADDING_PX * supersample - min(xs) * pixels_per_ldu,
            PADDING_PX * supersample - min(ys) * pixels_per_ldu,
        )
        return cls(image, ImageDraw.Draw(image), view, area, pixels_per_ldu, offset, supersample)

    def point(self, x: float, y: float, z: float) -> Point2:
        """Weltpunkt (LDU) → Pixel auf der vergrößerten Fläche."""
        px, py = self.view.project(x, y, z)
        return px * self.scale + self.offset[0], py * self.scale + self.offset[1]

    def polygon(self, corners: Sequence[Point3], fill: RgbTuple) -> None:
        self.draw.polygon([self.point(*corner) for corner in corners], fill=fill)

    def line(
        self, start: tuple[float, float, float], end: tuple[float, float, float], rgb: RgbTuple
    ) -> None:
        width = max(1, round(self.scale * 0.6))
        self.draw.line([self.point(*start), self.point(*end)], fill=rgb, width=width)

    def draw_baseplate(self, rgb: RgbTuple) -> None:
        """Dünne Platte unter dem ganzen Bereich (Oberseite, Vorder- und rechte Seite)."""
        x0, x1 = self.area.col * LDU_PER_STUD, (self.area.col + self.area.cols) * LDU_PER_STUD
        z0, z1 = self.area.row * LDU_PER_STUD, (self.area.row + self.area.rows) * LDU_PER_STUD
        bottom = -BASEPLATE_THICKNESS
        self.polygon(
            [(x0, bottom, z1), (x1, bottom, z1), (x1, 0, z1), (x0, 0, z1)], shade(rgb, FRONT_SHADE)
        )
        if self.view.shows_side_faces:
            side = [(x1, bottom, z0), (x1, bottom, z1), (x1, 0, z1), (x1, 0, z0)]
            self.polygon(side, shade(rgb, SIDE_SHADE))
        self.polygon([(x0, 0, z0), (x1, 0, z0), (x1, 0, z1), (x0, 0, z1)], rgb)

    def draw_cell(self, cell: Cell, content: RenderCell, cells: Mapping[Cell, RenderCell]) -> None:
        """Eine Zelle als Säule: sichtbare Seiten, Oberseite, Kanten, ggf. Noppe."""
        row, col = cell
        rgb = content.rgb
        x0, z0 = col * LDU_PER_STUD, row * LDU_PER_STUD
        x1, z1, top = x0 + LDU_PER_STUD, z0 + LDU_PER_STUD, PLATE_HEIGHT
        front_open = (row + 1, col) not in cells
        right_open = self.view.shows_side_faces and (row, col + 1) not in cells
        if front_open:  # Vorderseite nur sichtbar, wenn davor nichts steht
            self.polygon(
                [(x0, 0, z1), (x1, 0, z1), (x1, top, z1), (x0, top, z1)], shade(rgb, FRONT_SHADE)
            )
        if right_open:
            self.polygon(
                [(x1, 0, z0), (x1, 0, z1), (x1, top, z1), (x1, top, z0)], shade(rgb, SIDE_SHADE)
            )
        self.polygon([(x0, top, z0), (x1, top, z0), (x1, top, z1), (x0, top, z1)], rgb)
        self._draw_edges(
            cell, content, cells, edge_color(rgb), front_open=front_open, right_open=right_open
        )
        if content.has_stud:
            self.draw_stud(cell, PLATE_HEIGHT, rgb)

    def _draw_edges(
        self,
        cell: Cell,
        content: RenderCell,
        cells: Mapping[Cell, RenderCell],
        rgb: RgbTuple,
        *,
        front_open: bool,
        right_open: bool,
    ) -> None:
        """Kanten nur an Teilgrenzen, damit lange Teile als ein Stück erscheinen."""
        row, col = cell
        x0, z0 = col * LDU_PER_STUD, row * LDU_PER_STUD
        x1, z1, top = x0 + LDU_PER_STUD, z0 + LDU_PER_STUD, PLATE_HEIGHT

        def boundary(neighbor: Cell) -> bool:
            other = cells.get(neighbor)
            return other is None or other.part_id != content.part_id

        top_edges = {
            (row - 1, col): ((x0, top, z0), (x1, top, z0)),
            (row + 1, col): ((x0, top, z1), (x1, top, z1)),
            (row, col - 1): ((x0, top, z0), (x0, top, z1)),
            (row, col + 1): ((x1, top, z0), (x1, top, z1)),
        }
        for neighbor, (start, end) in top_edges.items():
            if boundary(neighbor):
                self.line(start, end, rgb)
        if front_open:
            self.line((x0, 0, z1), (x1, 0, z1), rgb)
            if boundary((row, col - 1)):
                self.line((x0, 0, z1), (x0, top, z1), rgb)
        if right_open:
            self.line((x1, 0, z0), (x1, 0, z1), rgb)
            if boundary((row - 1, col)):
                self.line((x1, 0, z0), (x1, top, z0), rgb)
        # Vordere rechte Ecke: nur dort, wo das Teil rechts bzw. vorne endet
        if (front_open and boundary((row, col + 1))) or (right_open and boundary((row + 1, col))):
            self.line((x1, 0, z1), (x1, top, z1), rgb)

    def draw_highlight(
        self, cell: Cell, content: RenderCell, cells: Mapping[Cell, RenderCell]
    ) -> None:
        """Orange Umrandung entlang der Teilgrenzen auf der Oberseite einer Zelle."""
        row, col = cell
        x0, z0 = col * LDU_PER_STUD, row * LDU_PER_STUD
        x1, z1, top = x0 + LDU_PER_STUD, z0 + LDU_PER_STUD, PLATE_HEIGHT
        edges = {
            (row - 1, col): ((x0, top, z0), (x1, top, z0)),
            (row + 1, col): ((x0, top, z1), (x1, top, z1)),
            (row, col - 1): ((x0, top, z0), (x0, top, z1)),
            (row, col + 1): ((x1, top, z0), (x1, top, z1)),
        }
        width = max(2, round(self.scale * HIGHLIGHT_WIDTH_FACTOR))
        for neighbor, (start, end) in edges.items():
            other = cells.get(neighbor)
            if other is None or other.part_id != content.part_id:
                self.draw.line(
                    [self.point(*start), self.point(*end)],
                    fill=HIGHLIGHT_RGB,
                    width=width,
                    joint="curve",
                )

    def draw_stud(self, cell: Cell, base_y: float, rgb: RgbTuple) -> None:
        """Noppe als Zylinder: untere Ellipse, Mantel, obere Ellipse."""
        row, col = cell
        center_x, center_z = (col + 0.5) * LDU_PER_STUD, (row + 0.5) * LDU_PER_STUD
        radius_x = STUD_RADIUS * self.scale
        radius_y = radius_x * self.view.stud_squash
        bottom = self.point(center_x, base_y, center_z)
        top = self.point(center_x, base_y + STUD_HEIGHT, center_z)
        side = shade(rgb, STUD_SIDE_SHADE)
        self.draw.ellipse(_ellipse_box(bottom, radius_x, radius_y), fill=side)
        self.draw.rectangle(
            (bottom[0] - radius_x, top[1], bottom[0] + radius_x, bottom[1]), fill=side
        )
        width = max(1, round(self.scale * 0.4))
        self.draw.ellipse(
            _ellipse_box(top, radius_x, radius_y),
            fill=shade(rgb, STUD_TOP_SHADE),
            outline=edge_color(rgb),
            width=width,
        )

    def finish(self) -> Image.Image:
        """Verkleinert um den Supersampling-Faktor (Kantenglättung).

        ``reduce`` mittelt je ``supersample`` × ``supersample`` Pixel zu einem; das ist
        genau die richtige Filterung für Supersampling und viel schneller als LANCZOS.
        """
        return self.image if self.supersample == 1 else self.image.reduce(self.supersample)


def _ellipse_box(
    center: Point2, radius_x: float, radius_y: float
) -> tuple[float, float, float, float]:
    return center[0] - radius_x, center[1] - radius_y, center[0] + radius_x, center[1] + radius_y


def shade(rgb: RgbTuple, factor: float) -> RgbTuple:
    """Multipliziert alle Kanäle mit ``factor`` (begrenzt auf 0–255)."""
    red, green, blue = (min(RGB_MAX, round(channel * factor)) for channel in rgb)
    return red, green, blue


def edge_color(rgb: RgbTuple) -> RgbTuple:
    """Kantenfarbe: dunkler als die Fläche, bei sehr dunklen Farben heller."""
    if max(rgb) < DARK_LIMIT:
        red, green, blue = (channel + EDGE_LIGHTEN_DARK for channel in rgb)
        return red, green, blue
    return shade(rgb, EDGE_DARKEN)
