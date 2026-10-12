"""Abschnitte: Das Mosaik wird in quadratische Bauabschnitte (z. B. 16 × 16) geteilt.

Pipeline (Zerlegung pro Abschnitt), LDraw-Bauschritte und Bauanleitung nutzen
dieselbe Einteilung, damit Anleitung und Studio-Modell zusammenpassen.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import groupby

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


@dataclass(frozen=True, slots=True)
class BuildStep:
    """Ein Bauschritt: einige Zeilen eines Abschnitts.

    Attributes:
        section: Der Abschnitt, zu dem der Schritt gehört.
        block: Nummer des Zeilenblocks innerhalb des Abschnitts (ab 0).
        placements: Die neuen Teile dieses Schritts, sortiert nach Farbe, dann Position.
    """

    section: Section
    block: int
    placements: tuple[Placement, ...]


def build_steps(
    placements: Sequence[Placement],
    *,
    width: int,
    height: int,
    section_studs: int,
    rows_per_step: int,
) -> tuple[BuildStep, ...]:
    """Teilt die Teile in Bauschritte: erst nach Abschnitt, darin nach Zeilenblöcken.

    Ein Teil gehört zum Abschnitt und Zeilenblock seiner Startzelle. Innerhalb eines
    Schritts wird nach Farbe, dann Position sortiert, damit man eine Farbe nach der
    anderen verbauen kann. Leere Schritte entfallen. LDraw-Datei und Bauanleitung
    nutzen genau diese Schritte.
    """
    sections = split_sections(width, height, section_studs)

    def step_key(placement: Placement) -> tuple[int, int]:
        index = section_index(placement.row, placement.col, width, section_studs)
        # % ist der Rest der Division: Zeile innerhalb des Abschnitts; // teilt in Blöcke
        return index, (placement.row % section_studs) // rows_per_step

    def sort_key(placement: Placement) -> tuple[int, int, int, int, int]:
        return (*step_key(placement), placement.color.id, placement.row, placement.col)

    ordered = sorted(placements, key=sort_key)
    # groupby fasst aufeinanderfolgende Elemente mit gleichem Schlüssel zusammen
    # (funktioniert nur, weil vorher nach demselben Schlüssel sortiert wurde)
    return tuple(
        BuildStep(sections[index], block, tuple(group))
        for (index, block), group in groupby(ordered, key=step_key)
    )
