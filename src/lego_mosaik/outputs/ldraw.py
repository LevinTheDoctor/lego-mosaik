"""LDraw-Datei (``.ldr``) mit Bauschritten für Bricklink Studio.

LDraw-Koordinaten: 1 Noppe = 20 LDU, die y-Achse zeigt nach *unten* (−y = oben).
Das ganze Mosaik liegt in einer Ebene (y = 0), Bildzeilen laufen entlang +z,
Bildspalten entlang +x.
"""

import logging
from collections.abc import Iterable, Sequence
from itertools import groupby
from pathlib import Path

from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.domain.models import PLATE_HEIGHT_LDU, LegoColor, LongAxis, Mosaic, Placement

logger = logging.getLogger(__name__)

FILE_NAME = "mosaik.ldr"
LDU_PER_STUD = 20
LAYER_Y = 0  # alle Teile in einer Lage
# Rotationsmatrizen (zeilenweise a b c d e f g h i)
IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
ROTATE_Y_90 = (0, 0, 1, 0, 1, 0, -1, 0, 0)  # 90° um die y-Achse: x → z

# Grundplatte (nur mit include_baseplate): Rebrickable/LDraw 3811 = "Baseplate 32 x 32"
BASEPLATE_FILE = "3811.dat"
BASEPLATE_STUDS = 32
BASEPLATE_COLOR_CODE = 71  # Light Bluish Gray


class LdrawWriter:
    """Schreibt ``mosaik.ldr``.

    Args:
        rows_per_step: Nach so vielen Zeilen beginnt ein neuer Bauschritt.
        include_baseplate: Grundplatte(n) 32 × 32 unter das Mosaik legen.
    """

    name = "ldraw"

    def __init__(self, *, rows_per_step: int, include_baseplate: bool) -> None:
        self._rows_per_step = rows_per_step
        self._include_baseplate = include_baseplate

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Erzeugt die Zeilen und schreibt sie mit Windows-freundlichen Zeilenenden."""
        lines = ldraw_lines(mosaic, self._rows_per_step, include_baseplate=self._include_baseplate)
        path = target_dir / FILE_NAME
        # LDraw-Dateien sind ASCII/UTF-8; CRLF ("\r\n") ist der übliche Zeilenumbruch
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
        logger.info("LDraw-Datei geschrieben: %s", path)
        return path


def ldraw_lines(mosaic: Mosaic, rows_per_step: int, *, include_baseplate: bool) -> list[str]:
    """Alle Zeilen der LDraw-Datei: Kopf, optional Grundplatte, Teile in Bauschritten."""
    lines = header_lines(mosaic.title)
    if include_baseplate:
        lines += baseplate_lines(mosaic.width, mosaic.height)
        lines.append("0 STEP")
    for _, step in group_steps(mosaic.placements, rows_per_step):
        lines += [placement_line(placement) for placement in step]
        lines.append("0 STEP")
    return lines


def header_lines(title: str) -> list[str]:
    """Pflichtkopf einer LDraw-Modelldatei."""
    return [
        f"0 {title}",
        f"0 Name: {FILE_NAME}",
        "0 Author: lego-mosaik",
        "0 !LDRAW_ORG Unofficial_Model",
    ]


def group_steps(
    placements: Iterable[Placement], rows_per_step: int
) -> list[tuple[int, list[Placement]]]:
    """Teilt die Teile in Bauschritte: ein Teil gehört zum Schritt seiner Startzeile.

    Innerhalb eines Schritts wird nach Farbe, dann Position sortiert, damit man
    eine Farbe nach der anderen verbauen kann. Leere Schritte entfallen.
    """

    def sort_key(placement: Placement) -> tuple[int, int, int, int]:
        # // ist ganzzahlige Division: Zeilen 0–3 → Schritt 0, 4–7 → Schritt 1, …
        return placement.row // rows_per_step, placement.color.id, placement.row, placement.col

    ordered = sorted(placements, key=sort_key)
    # groupby fasst aufeinanderfolgende Elemente mit gleichem Schlüssel zusammen
    # (funktioniert nur, weil vorher nach demselben Schlüssel sortiert wurde)
    return [
        (step, list(group))
        for step, group in groupby(ordered, key=lambda p: p.row // rows_per_step)
    ]


def placement_line(placement: Placement) -> str:
    """Zeile Typ 1: ``1 <farbe> <x> <y> <z> <a b c d e f g h i> <datei>``."""
    # Mittelpunkt des Teils: linke Kante + halbe Ausdehnung, in LDU umgerechnet
    x = (placement.col + placement.cols_spanned / 2) * LDU_PER_STUD
    z = (placement.row + placement.rows_spanned / 2) * LDU_PER_STUD
    matrix = rotation_matrix(placement)
    color_code = ldraw_color_code(placement.color)
    return _type1_line(color_code, (x, LAYER_Y, z), matrix, placement.part.ldraw_file)


def ldraw_color_code(color: LegoColor) -> int:
    """LDraw-Farbcode einer Farbe.

    Raises:
        CatalogError: Wenn für die Farbe kein sicherer LDraw-Code bekannt ist.
    """
    if color.ldraw_code is None:
        raise CatalogError(f"Für die Farbe {color.name} ist kein LDraw-Farbcode bekannt.")
    return color.ldraw_code


def rotation_matrix(placement: Placement) -> tuple[int, ...]:
    """Drehung, damit die lange Seite in die gewünschte Richtung zeigt.

    Gewünscht ist: ungedreht entlang x (Spalten), gedreht entlang z (Zeilen).
    Liegt die lange Seite in der LDraw-Datei schon so, braucht es keine Drehung.
    """
    desired = LongAxis.Z if placement.rotated else LongAxis.X
    return IDENTITY if placement.part.ldraw_long_axis is desired else ROTATE_Y_90


def baseplate_lines(width: int, height: int) -> list[str]:
    """Grundplatten 32 × 32 lückenlos unter dem Mosaik (ragen ggf. über den Rand)."""
    half = BASEPLATE_STUDS / 2
    # -(-a // b) rundet die Division auf: 48 Noppen brauchen 2 Grundplatten
    columns, rows = -(-width // BASEPLATE_STUDS), -(-height // BASEPLATE_STUDS)
    return [
        # y = Plate-Höhe: Die Oberkante der Grundplatte liegt an der Unterkante der Mosaikteile
        _type1_line(
            BASEPLATE_COLOR_CODE,
            (
                (bx * BASEPLATE_STUDS + half) * LDU_PER_STUD,
                PLATE_HEIGHT_LDU,
                (bz * BASEPLATE_STUDS + half) * LDU_PER_STUD,
            ),
            IDENTITY,
            BASEPLATE_FILE,
        )
        for bz in range(rows)
        for bx in range(columns)
    ]


def _type1_line(
    color_code: int, position: Sequence[float], matrix: Sequence[int], file_name: str
) -> str:
    """Formatiert eine Typ-1-Zeile; ganze Zahlen ohne ".0" (``:g`` = kompakte Darstellung)."""
    numbers = " ".join(f"{value:g}" for value in (*position, *matrix))
    return f"1 {color_code} {numbers} {file_name}"
