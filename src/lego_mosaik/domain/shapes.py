"""Umrisse der Formteile von oben (Rundungen, Dreiecke, Cut Corner, Wedge Plates).

Koordinaten in Noppen, x = Spalte nach rechts, y = Zeile nach unten, Ursprung oben
links in der Box des Teils. Die Grundlage (``turns = 0``) entspricht genau der Lage in
der LDraw-Datei (``PartType.box`` gibt deren Breite und Höhe an):

- Eckige Umrisse (Dreieck, Cut Corner, Wedge Plates) stehen als Polygon in
  ``daten/teil_geometrie.csv`` (Spalte ``outline``), gemessen mit der LDraw-Bibliothek
  von Bricklink Studio (``scripts/ldraw_achsen_pruefen.py``).
- Rundungen werden hier berechnet:

=========== ======= ==================================================================
Umriss      Teil    Lage bei ``turns = 0``
=========== ======= ==================================================================
ROUND       98138   Kreis in der Mitte
QUARTER     25269   rechter Winkel unten links, Bogen von oben links nach unten rechts
HALF        24246   gerade Kante unten, Rundung oben
=========== ======= ==================================================================

Eine Vierteldrehung im Uhrzeigersinn bildet (x, y) in einer Box der Höhe h auf
(h − y, x) ab; Breite und Höhe der Box tauschen dabei. Alle Umrisse sind konvex; das
hält Punkttest und Zuschnitt einfach.
"""

import math
from collections.abc import Mapping, Sequence

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.models import Outline, PartShape, PartType, Point

ARC_POINTS = 13  # Punkte auf einem Viertelkreis (inklusive beider Enden)
STUD_RADIUS = 0.3  # Radius einer Noppe in Noppen (6 von 20 LDU)
SAMPLES_PER_SUBPIXEL = 4  # Abtastpunkte pro Richtung und Teilpixel (für Kantenglättung)
TURNS = 4  # vier Vierteldrehungen ergeben wieder die Ausgangslage
EPSILON = 1e-9


def _arc(center: Point, radius: float, start_deg: float, end_deg: float) -> list[Point]:
    """Punkte auf einem Kreisbogen; Winkel 0° zeigt nach oben, positiv im Uhrzeigersinn."""
    steps = max(2, round(abs(end_deg - start_deg) / 90 * (ARC_POINTS - 1)) + 1)
    points = []
    for index in range(steps):
        angle = math.radians(start_deg + (end_deg - start_deg) * index / (steps - 1))
        # y wächst nach unten: „oben“ ist −y, deshalb −cos
        points.append((center[0] + radius * math.sin(angle), center[1] - radius * math.cos(angle)))
    return points


# Berechnete Umrisse (alle 1 × 1) bei turns = 0
_CURVES: Mapping[PartShape, Outline] = {
    PartShape.ROUND: tuple(_arc((0.5, 0.5), 0.5, 0.0, 360.0)[:-1]),
    PartShape.QUARTER: ((0.0, 1.0), *_arc((0.0, 1.0), 1.0, 0.0, 90.0)),
    PartShape.HALF: (*_arc((0.5, 0.5), 0.5, -90.0, 90.0), (1.0, 1.0), (0.0, 1.0)),
}
CURVED_SHAPES = frozenset(_CURVES)
# Umrisse, die bei einer Drehung gleich bleiben, brauchen nur eine Ausrichtung
SYMMETRIC_SHAPES = frozenset({PartShape.SQUARE, PartShape.ROUND})


def base_outline(part: PartType) -> Outline:
    """Umriss bei ``turns = 0``: berechnete Rundung, Polygon aus der Tabelle oder Rechteck."""
    if part.shape in _CURVES:
        return _CURVES[part.shape]
    if part.outline:
        return part.outline
    cols, rows = part.box
    return ((0.0, 0.0), (float(cols), 0.0), (float(cols), float(rows)), (0.0, float(rows)))


def part_turns(part: PartType) -> range:
    """Sinnvolle Drehungen: symmetrische Umrisse nur 0, alle anderen 0 bis 3."""
    return range(1) if part.shape in SYMMETRIC_SHAPES else range(TURNS)


def box(part: PartType, turns: int = 0) -> tuple[int, int]:
    """(Zeilen, Spalten) der Box nach ``turns`` Vierteldrehungen."""
    cols, rows = part.box
    return (cols, rows) if turns % 2 else (rows, cols)


def outline(part: PartType, turns: int = 0) -> Outline:
    """Umriss in der Box des Teils nach ``turns`` Vierteldrehungen im Uhrzeigersinn."""
    points, (cols, rows) = base_outline(part), part.box
    for _ in range(turns % TURNS):
        points = tuple((rows - y, x) for x, y in points)
        cols, rows = rows, cols  # die alte Höhe ist die neue Breite
    return points


def cell_outline(part: PartType, turns: int, d_row: int, d_col: int) -> Outline | None:
    """Teil des Umrisses, der in der Zelle (``d_row``, ``d_col``) der Box liegt.

    Die Koordinaten sind relativ zur Zelle (0 bis 1). ``None``, wenn das Teil die Zelle
    nicht berührt. Zuschnitt nach Sutherland-Hodgman an den vier Zellkanten.
    """
    points = [(x - d_col, y - d_row) for x, y in outline(part, turns)]
    # Jede Kante der Zelle als (Achse, Grenze, innen ist größer?)
    for axis, limit, keep_greater in (
        (0, 0.0, True),
        (0, 1.0, False),
        (1, 0.0, True),
        (1, 1.0, False),
    ):
        points = _clip(points, axis, limit, keep_greater)
        if not points:
            return None
    return tuple(points) if _area(points) > EPSILON else None


def _clip(points: Sequence[Point], axis: int, limit: float, keep_greater: bool) -> list[Point]:
    """Schneidet ein Polygon an einer achsenparallelen Geraden ab (Sutherland-Hodgman)."""

    def inside(point: Point) -> bool:
        return point[axis] >= limit - EPSILON if keep_greater else point[axis] <= limit + EPSILON

    result: list[Point] = []
    for index, current in enumerate(points):
        previous = points[index - 1]  # index − 1 = −1 ist das letzte Element (geschlossen)
        if inside(current):
            if not inside(previous):
                result.append(_intersect(previous, current, axis, limit))
            result.append(current)
        elif inside(previous):
            result.append(_intersect(previous, current, axis, limit))
    return result


def _intersect(start: Point, end: Point, axis: int, limit: float) -> Point:
    """Schnittpunkt der Strecke mit der Geraden ``Koordinate[axis] == limit``."""
    share = (limit - start[axis]) / (end[axis] - start[axis])
    return start[0] + share * (end[0] - start[0]), start[1] + share * (end[1] - start[1])


def _area(points: Sequence[Point]) -> float:
    """Fläche eines Polygons (Gaußsche Trapezformel)."""
    return abs(polygon_signed_area(points))


def polygon_signed_area(points: Sequence[Point]) -> float:
    """Vorzeichenbehaftete Fläche; positiv heißt im Uhrzeigersinn (weil y nach unten zeigt)."""
    total = 0.0
    for index, (x1, y1) in enumerate(points):
        x0, y0 = points[index - 1]
        total += x0 * y1 - x1 * y0
    return total / 2


def coverage(part: PartType, turns: int, subsample: int) -> npt.NDArray[np.float64]:
    """Anteil jedes Teilpixels, den das Teil bedeckt (0 bis 1).

    Die Box (Zeilen × Spalten Noppen) wird in Teilpixel zu ``subsample`` × ``subsample``
    pro Noppe geteilt; jedes Teilpixel wird mit 4 × 4 Punkten abgetastet. So entstehen
    weiche Kanten, und die Formwahl bewertet schräge und runde Konturen fair.
    """
    rows, cols = box(part, turns)
    samples = SAMPLES_PER_SUBPIXEL
    step = subsample * samples
    # Mittelpunkte aller Abtastpunkte in Noppen-Koordinaten
    xs, ys = np.meshgrid(
        (np.arange(cols * step) + 0.5) / step, (np.arange(rows * step) + 0.5) / step
    )
    inside = inside_convex(outline(part, turns), xs, ys)
    # reshape + mean: je samples × samples Abtastpunkte zu einem Teilpixel mitteln
    shape = (rows * subsample, samples, cols * subsample, samples)
    return inside.reshape(shape).mean(axis=(1, 3))


def inside_convex(
    points: Sequence[Point], xs: npt.NDArray[np.float64], ys: npt.NDArray[np.float64]
) -> npt.NDArray[np.bool_]:
    """Punkttest für ein konvexes Polygon: Der Punkt liegt auf der Innenseite aller Kanten."""
    sign = 1.0 if polygon_signed_area(points) > 0 else -1.0
    result = np.ones(xs.shape, dtype=bool)
    for index, (x1, y1) in enumerate(points):
        x0, y0 = points[index - 1]
        # Kreuzprodukt (Kante × Punkt): Vorzeichen sagt, auf welcher Seite der Punkt liegt
        cross = (x1 - x0) * (ys - y0) - (y1 - y0) * (xs - x0)
        result &= sign * cross >= -EPSILON
    return result


def stud_visible(part: PartType, turns: int, d_row: int, d_col: int) -> bool:
    """True, wenn auf der Zelle eine ganze Noppe des Teils Platz hat.

    Plates wie die Wedge Plates haben nur dort Noppen, wo der Umriss die Noppe
    vollständig enthält (Abstand zur Mitte der Zelle mindestens ``STUD_RADIUS``).
    """
    points = outline(part, turns)
    center = (d_col + 0.5, d_row + 0.5)
    if not inside_convex(points, np.array([center[0]]), np.array([center[1]]))[0]:
        return False
    return all(
        _distance_to_line(center, points[index - 1], point) >= STUD_RADIUS - EPSILON
        for index, point in enumerate(points)
    )


def _distance_to_line(point: Point, start: Point, end: Point) -> float:
    """Abstand eines Punkts von der Geraden durch ``start`` und ``end``."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    return abs(dx * (point[1] - start[1]) - dy * (point[0] - start[0])) / math.hypot(dx, dy)


def parse_outline(text: str) -> Outline:
    """Liest ``"0 0; 1 0; 2 1"`` (Punkte in Noppen) in ein Polygon.

    Raises:
        ValueError: Bei weniger als drei Punkten oder falschem Format.
    """
    points = tuple(
        (float(x), float(y)) for x, y in (pair.split() for pair in text.split(";") if pair.strip())
    )
    if len(points) < 3:
        raise ValueError("Ein Umriss braucht mindestens drei Punkte")
    return points
