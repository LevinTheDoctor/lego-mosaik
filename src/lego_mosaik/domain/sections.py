"""Abschnitte: Das Mosaik wird in quadratische Bauabschnitte (z. B. 16 × 16) geteilt.

Pipeline (Zerlegung pro Abschnitt), LDraw-Bauschritte und Bauanleitung nutzen
dieselbe Einteilung, damit Anleitung und Studio-Modell zusammenpassen.
"""

from collections.abc import Iterable
from dataclasses import dataclass

from lego_mosaik.domain.models import Placement


@dataclass(frozen=True, slots=True)
class Section:
    """Ein rechteckiger Bauabschnitt.

    Attributes:
        number: Laufende Nummer ab 1, zeilenweise von oben links.
        row: Oberste Zeile.
        col: Linke Spalte.
        rows: Höhe in Noppen (am Rand ggf. kleiner als die Abschnittsgröße).
        cols: Breite in Noppen.
    """

    number: int
    row: int
    col: int
    rows: int
    cols: int

    def contains(self, row: int, col: int) -> bool:
        """True, wenn die Zelle in diesem Abschnitt liegt."""
        return self.row <= row < self.row + self.rows and self.col <= col < self.col + self.cols


def split_sections(width: int, height: int, size: int) -> tuple[Section, ...]:
    """Teilt ``width`` × ``height`` in Abschnitte der Kantenlänge ``size``."""
    starts = [(row, col) for row in range(0, height, size) for col in range(0, width, size)]
    return tuple(
        # min(...) kürzt Abschnitte am rechten bzw. unteren Rand
        Section(number, row, col, min(size, height - row), min(size, width - col))
        for number, (row, col) in enumerate(starts, start=1)
    )


def section_index(row: int, col: int, width: int, size: int) -> int:
    """Index (ab 0) des Abschnitts, in dem die Zelle liegt (zeilenweise gezählt)."""
    sections_per_row = -(-width // size)  # aufgerundete Division
    return (row // size) * sections_per_row + col // size


def placements_in(section: Section, placements: Iterable[Placement]) -> list[Placement]:
    """Alle Teile, deren Startzelle im Abschnitt liegt."""
    return [p for p in placements if section.contains(p.row, p.col)]
