"""LDraw-Datei (``.ldr``) mit Bauschritten für Bricklink Studio.

LDraw-Koordinaten: 1 Noppe = 20 LDU, die y-Achse zeigt nach *unten* (−y = oben).
Bildzeilen laufen entlang +z, Bildspalten entlang +x. Die Oberkante des Unterbaus
liegt bei y = 8, das Bild (Plates und Tiles) also genau eine Plate-Höhe darüber.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.domain.models import (
    PLATE_HEIGHT_LDU,
    LegoColor,
    LongAxis,
    Mosaic,
    PartKind,
    Placement,
)
from lego_mosaik.domain.sections import BuildStep, StepKind, assembly_steps

logger = logging.getLogger(__name__)

FILE_NAME = "mosaik.ldr"
LDU_PER_STUD = 20
# Rotationsmatrizen (zeilenweise a b c d e f g h i)
IDENTITY = (1, 0, 0, 0, 1, 0, 0, 0, 1)
ROTATE_Y_90 = (0, 0, 1, 0, 1, 0, -1, 0, 0)  # 90° um die y-Achse: x → z
# Formteile: 0 bis 3 Vierteldrehungen im Uhrzeigersinn, von oben gesehen (x → z → −x)
QUARTER_TURNS = (
    IDENTITY,
    (0, 0, -1, 0, 1, 0, 1, 0, 0),
    (-1, 0, 0, 0, 1, 0, 0, 0, -1),
    (0, 0, 1, 0, 1, 0, -1, 0, 0),
)
# Oberkante des Unterbaus. Plates, Tiles und Unterbau-Platten haben ihren Ursprung
# an der Oberkante; eine Plate direkt auf dem Unterbau hat damit y = 0 (+y = nach unten)
SUBSTRATE_TOP_Y = PLATE_HEIGHT_LDU
# Kommentar vor jedem Schritt (Studio ignoriert ihn, beim Lesen der Datei hilft er)
_STEP_COMMENTS = {
    StepKind.CONNECTORS: "Verbinder (Technic-Pins)",
    StepKind.BOND: "Verbindungslage",
    StepKind.FRAME: "Rahmen",
}
_SUBSTRATE_COMMENTS = {
    PartKind.BASEPLATE: "Grundplatten",
    PartKind.PANEL: "LEGO-Art-Paneele",
    PartKind.PLATE: "Große Platten",
}


class LdrawWriter:
    """Schreibt ``mosaik.ldr``.

    Args:
        rows_per_step: Nach so vielen Zeilen eines Abschnitts beginnt ein neuer Bauschritt.
        section_studs: Kantenlänge der Bauabschnitte (wie in der Bauanleitung).
        connector_depth_ldu: Tiefe der Pin-Löcher unter der Oberkante der Art-Paneele.
    """

    name = "ldraw"

    def __init__(
        self, *, rows_per_step: int, section_studs: int, connector_depth_ldu: int = 16
    ) -> None:
        self._rows_per_step = rows_per_step
        self._section_studs = section_studs
        self._connector_depth = connector_depth_ldu

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Erzeugt die Zeilen und schreibt sie mit Windows-freundlichen Zeilenenden."""
        lines = ldraw_lines(mosaic, self._rows_per_step, self._section_studs, self._connector_depth)
        path = target_dir / FILE_NAME
        # LDraw-Dateien sind ASCII/UTF-8; CRLF ("\r\n") ist der übliche Zeilenumbruch
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8", newline="")
        logger.info("LDraw-Datei geschrieben: %s", path)
        return path


def ldraw_lines(
    mosaic: Mosaic, rows_per_step: int, section_studs: int, connector_depth_ldu: int = 16
) -> list[str]:
    """Alle Zeilen: Kopf, dann pro Bauschritt die Teile und ein ``0 STEP``.

    Die Schritte sind dieselben wie in der Bauanleitung (``assembly_steps``):
    Unterbau, Verbinder, Rahmen-Unterbau, Rahmen, dann das Bild abschnittsweise.
    """
    lines = header_lines(mosaic.title)
    steps = assembly_steps(mosaic, section_studs=section_studs, rows_per_step=rows_per_step)
    previous_comment = ""
    for step in steps:
        comment = step_comment(step)
        if comment != previous_comment:  # ein Kommentar pro Abschnitt, nicht pro Schritt
            lines.append(f"0 // {comment}")
            previous_comment = comment
        lines += [
            placement_line(placement, y=_y(step.kind, placement, connector_depth_ldu))
            for placement in step.placements
        ]
        lines.append("0 STEP")
    return lines


def step_comment(step: BuildStep) -> str:
    """Kurzer deutscher Name des Schritts, z. B. „Abschnitt 3“."""
    match step.kind:
        case StepKind.SECTION | StepKind.RELIEF if step.section is not None:
            return f"Abschnitt {step.section.number}"
        case StepKind.SUBSTRATE:
            return _SUBSTRATE_COMMENTS.get(step.placements[0].part.kind, "Unterbau")
        case StepKind.FRAME_SUPPORT:
            return f"Rahmen-Unterbau, Lage {step.level + 1}"
        case _:
            return _STEP_COMMENTS.get(step.kind, step.kind.value)


def _y(kind: StepKind, placement: Placement, connector_depth_ldu: int) -> float:
    """Höhe des Teilursprungs (y) je nach Art des Schritts."""
    if kind is StepKind.SUBSTRATE:
        return SUBSTRATE_TOP_Y  # Ursprung = Oberkante der Platte
    if kind is StepKind.CONNECTORS:
        return SUBSTRATE_TOP_Y + connector_depth_ldu  # Pin-Mitte steckt seitlich im Paneel
    return part_y(placement)


def part_y(placement: Placement) -> float:
    """y der Oberkante eines gestapelten Teils: Unterbau-Oberkante − Unterkante − Höhe.

    Beispiele: Plate auf dem Unterbau 8 − 0 − 8 = 0; Tile auf einer Stütz-Plate
    8 − 8 − 8 = −8 (eine Plate höher); Brick 8 − 0 − 24 = −16.
    """
    return SUBSTRATE_TOP_Y - placement.bottom_ldu - placement.part.height_ldu


def header_lines(title: str) -> list[str]:
    """Pflichtkopf einer LDraw-Modelldatei."""
    return [
        f"0 {title}",
        f"0 Name: {FILE_NAME}",
        "0 Author: lego-mosaik",
        "0 !LDRAW_ORG Unofficial_Model",
    ]


def placement_line(placement: Placement, *, y: float) -> str:
    """Zeile Typ 1: ``1 <farbe> <x> <y> <z> <a b c d e f g h i> <datei>``."""
    # Mittelpunkt des Teils: linke Kante + halbe Ausdehnung, in LDU umgerechnet
    x = (placement.col + placement.cols_spanned / 2) * LDU_PER_STUD
    z = (placement.row + placement.rows_spanned / 2) * LDU_PER_STUD
    matrix = rotation_matrix(placement)
    color_code = ldraw_color_code(placement.color)
    return _type1_line(color_code, (x, y, z), matrix, placement.part.ldraw_file)


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
    Formteile sind quadratisch und drehen in Vierteln (``Placement.turns``); ihre
    Grundlage in ``domain/shapes.py`` entspricht der Lage in der LDraw-Datei.
    """
    if placement.part.is_shaped:
        return QUARTER_TURNS[placement.turns % len(QUARTER_TURNS)]
    desired = LongAxis.Z if placement.rotated else LongAxis.X
    return IDENTITY if placement.part.ldraw_long_axis is desired else ROTATE_Y_90


def _type1_line(
    color_code: int, position: Sequence[float], matrix: Sequence[int], file_name: str
) -> str:
    """Formatiert eine Typ-1-Zeile; ganze Zahlen ohne ".0" (``:g`` = kompakte Darstellung)."""
    numbers = " ".join(f"{value:g}" for value in (*position, *matrix))
    return f"1 {color_code} {numbers} {file_name}"
