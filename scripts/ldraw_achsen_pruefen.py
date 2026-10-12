"""Prüft ``ldraw_long_axis`` in ``daten/teil_geometrie.csv`` gegen echte LDraw-Teile.

Aufruf (Standard: LDraw-Bibliothek aus Bricklink Studio unter macOS)::

    uv run python scripts/ldraw_achsen_pruefen.py
    uv run python scripts/ldraw_achsen_pruefen.py /pfad/zur/ldraw-bibliothek

Vorgehen: Für jedes nicht quadratische Teil wird die Bounding Box seiner Geometrie
berechnet, rekursiv über alle Unterdateien (Typ-1-Zeilen) inklusive deren
Verschiebung und Rotationsmatrix. Die längere Ausdehnung in der x-z-Ebene ist die
lange Seite des Teils.
"""

import csv
import functools
import sys
from pathlib import Path

DEFAULT_LIBRARY = Path("/Applications/Studio 2.0/ldraw")
GEOMETRY_FILE = Path(__file__).parent.parent / "daten" / "teil_geometrie.csv"
# Unterordner wie "s\\" oder "48\\" stehen im Namen; Studio legt neuere Teile (z. B. 25269)
# unter UnOfficial ab
SEARCH_DIRS = ("parts", "p", "models", "UnOfficial/parts", "UnOfficial/p")
GEOMETRY_TYPES = {"2": 2, "3": 3, "4": 4, "5": 2}  # Linientyp → Anzahl Punkte (Typ 5: 2 Punkte)

type Point = tuple[float, float, float]
type Box = tuple[Point, Point]  # (Minimum, Maximum)


def find_file(library: Path, name: str) -> Path | None:
    """Sucht eine LDraw-Datei; Namen nutzen "\\" als Trenner und sind nicht case-sensitiv."""
    relative = Path(*name.strip().replace("\\", "/").split("/"))
    for folder in SEARCH_DIRS:
        for candidate in (library / folder / relative, library / folder / str(relative).lower()):
            if candidate.is_file():
                return candidate
    return None


def bounding_box(library: Path, name: str) -> Box | None:
    """Bounding Box einer Datei samt aller Unterdateien (``None`` ohne Geometrie)."""
    return _cached_box(library, name.lower())


@functools.cache
def _cached_box(library: Path, name: str) -> Box | None:
    path = find_file(library, name)
    if path is None:
        return None
    points: list[Point] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        points += _line_points(library, line.split())
    return _box_of(points) if points else None


def _line_points(library: Path, fields: list[str]) -> list[Point]:
    """Punkte einer Zeile: direkte Geometrie oder die transformierte Box einer Unterdatei."""
    if not fields:
        return []
    if fields[0] in GEOMETRY_TYPES:
        values = [float(v) for v in fields[2 : 2 + 3 * GEOMETRY_TYPES[fields[0]]]]
        return [(values[i], values[i + 1], values[i + 2]) for i in range(0, len(values), 3)]
    if fields[0] == "1" and len(fields) >= 15:
        child = bounding_box(library, " ".join(fields[14:]))
        if child is None:
            return []
        numbers = [float(v) for v in fields[2:14]]
        return [_transform(corner, numbers) for corner in _corners(child)]
    return []


def _transform(point: Point, numbers: list[float]) -> Point:
    """Wendet ``x y z a b c d e f g h i`` einer Typ-1-Zeile auf einen Punkt an."""
    x, y, z, a, b, c, d, e, f, g, h, i = numbers
    px, py, pz = point
    # Matrix mal Punkt plus Verschiebung (so definiert es die LDraw-Spezifikation)
    return (
        a * px + b * py + c * pz + x,
        d * px + e * py + f * pz + y,
        g * px + h * py + i * pz + z,
    )


def _corners(box: Box) -> list[Point]:
    """Die acht Eckpunkte einer Box."""
    (x0, y0, z0), (x1, y1, z1) = box
    return [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]


def _box_of(points: list[Point]) -> Box:
    # zip(*points) dreht die Liste: alle x-Werte, alle y-Werte, alle z-Werte
    xs, ys, zs = zip(*points, strict=True)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def footprint(library: Path, name: str) -> list[tuple[float, float]]:
    """Umriss eines Teils von oben: konvexe Hülle aller Punkte in der x-z-Ebene (in LDU).

    Rekursiv über alle Unterdateien mit ihrer Verschiebung und Drehung. Noppen (über der
    Oberkante, y < 0) zählen nicht mit.
    """
    flat = {(round(x, 2), round(z, 2)) for x, y, z in _all_points(library, name.lower()) if y >= 0}
    return _hull(sorted(flat))


@functools.cache
def _all_points(library: Path, name: str) -> tuple[Point, ...]:
    path = find_file(library, name)
    if path is None:
        return ()
    points: list[Point] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = line.split()
        if fields and fields[0] in GEOMETRY_TYPES:
            points += _line_points(library, fields)
        elif fields and fields[0] == "1" and len(fields) >= 15:
            numbers = [float(v) for v in fields[2:14]]
            child = _all_points(library, " ".join(fields[14:]).lower())
            points += [_transform(point, numbers) for point in child]
    return tuple(points)


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Konvexe Hülle (Andrew's Monotone Chain), Punkte sortiert übergeben."""

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    upper: list[tuple[float, float]] = []
    for chain, ordered in ((lower, points), (upper, list(reversed(points)))):
        for point in ordered:
            while len(chain) >= 2 and cross(chain[-2], chain[-1], point) <= 0:
                chain.pop()
            chain.append(point)
    return lower[:-1] + upper[:-1]


def check(library: Path, geometry_file: Path = GEOMETRY_FILE) -> list[str]:
    """Gibt eine Liste deutscher Abweichungsmeldungen zurück (leer = alles korrekt)."""
    problems = []
    with geometry_file.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["width_studs"] == row["length_studs"]:
                continue  # quadratisch: Ausrichtung egal
            box = bounding_box(library, row["ldraw_file"])
            if box is None:
                problems.append(f"{row['part_num']}: {row['ldraw_file']} nicht gefunden")
                continue
            (x0, _, z0), (x1, _, z1) = box
            actual = "x" if x1 - x0 > z1 - z0 else "z"
            if actual != (row.get("ldraw_long_axis") or "x"):
                problems.append(f"{row['part_num']}: lange Seite liegt entlang {actual}")
    return problems


def main() -> None:
    """Prüft und beendet mit Code 1, wenn Abweichungen gefunden wurden."""
    library = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LIBRARY
    problems = check(library)
    for problem in problems:
        print(f"Abweichung: {problem}")
    print("Alle Achsen stimmen." if not problems else f"{len(problems)} Abweichungen.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
