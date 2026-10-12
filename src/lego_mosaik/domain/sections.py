"""Abschnitte und Bauschritte.

Das Bild wird in quadratische Bauabschnitte (z. B. 16 × 16) geteilt. Vorher kommen
Unterbau, Verbinder und Rahmen. Pipeline (Zerlegung pro Abschnitt), LDraw-Datei und
Bauanleitung nutzen dieselbe Einteilung, damit Anleitung und Studio-Modell zusammenpassen.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import groupby

from lego_mosaik.domain.models import PLATE_HEIGHT_LDU, Mosaic, Placement


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


def split_sections(width: int, height: int, size: int, *, offset: int = 0) -> tuple[Section, ...]:
    """Teilt ``width`` × ``height`` in Abschnitte der Kantenlänge ``size``.

    Args:
        width: Breite in Noppen.
        height: Höhe in Noppen.
        size: Kantenlänge eines Abschnitts.
        offset: Verschiebt die Grenzen um so viele Noppen nach rechts unten. Oben
            links entsteht dann ein schmalerer Abschnitt. So lassen sich Lagen „auf
            Lücke“ legen (Mauerverband): Ihre Fugen treffen nie die der anderen Lage.
    """
    row_starts, col_starts = _starts(height, size, offset), _starts(width, size, offset)
    # zip(starts, starts[1:] + [ende]) paart jeden Anfang mit dem nächsten → Länge
    row_spans = list(zip(row_starts, [*row_starts[1:], height], strict=True))
    col_spans = list(zip(col_starts, [*col_starts[1:], width], strict=True))
    cells = [(rows, cols) for rows in row_spans for cols in col_spans]
    return tuple(
        Section(number, row_start, col_start, row_end - row_start, col_end - col_start)
        for number, ((row_start, row_end), (col_start, col_end)) in enumerate(cells, start=1)
    )


def _starts(length: int, size: int, offset: int) -> list[int]:
    """Anfänge der Abschnitte: 0 und dann alle ``offset + k·size`` innerhalb der Länge."""
    # Ein set entfernt die doppelte 0 bei offset == 0; sorted gibt die Reihenfolge zurück
    return sorted({0, *range(offset % size, length, size)})


def section_index(row: int, col: int, width: int, size: int) -> int:
    """Index (ab 0) des Abschnitts, in dem die Zelle liegt (zeilenweise gezählt)."""
    sections_per_row = -(-width // size)  # aufgerundete Division
    return (row // size) * sections_per_row + col // size


def placements_in(section: Section, placements: Iterable[Placement]) -> list[Placement]:
    """Alle Teile, deren Startzelle im Abschnitt liegt."""
    return [p for p in placements if section.contains(p.row, p.col)]


class StepKind(StrEnum):
    """Art eines Bauschritts in der Reihenfolge, in der gebaut wird."""

    SUBSTRATE = "substrate"  # Grundplatten bzw. Paneele auslegen
    CONNECTORS = "connectors"  # Paneele mit Technic-Pins verbinden
    BOND = "bond"  # große Platten mit einer Lage Plates auf Lücke verbinden
    FRAME_SUPPORT = "frame_support"  # Plates unter einem erhöhten Rahmen
    FRAME = "frame"  # sichtbarer Rahmen
    RELIEF = "relief"  # Stützlage unter erhöhten Bildteilen eines Abschnitts
    SECTION = "section"  # einige Zeilen eines Bildabschnitts


@dataclass(frozen=True, slots=True)
class BuildStep:
    """Ein Bauschritt.

    Attributes:
        kind: Art des Schritts.
        placements: Die neuen Teile dieses Schritts, sortiert nach Farbe, dann Position.
        section: Bei Bild- und Reliefschritten der Abschnitt, sonst ``None``.
        block: Bei Bildschritten die Nummer des Zeilenblocks im Abschnitt (ab 0).
        level: Bei Rahmen-Unterbau und Relief die Lage (ab 0, von unten).
    """

    kind: StepKind
    placements: tuple[Placement, ...]
    section: Section | None = None
    block: int = 0
    level: int = 0


def assembly_steps(
    mosaic: Mosaic, *, section_studs: int, rows_per_step: int
) -> tuple[BuildStep, ...]:
    """Alle Bauschritte von unten nach oben: Unterbau, Verbinder, Rahmen, Bild.

    Der Rahmen kommt vor dem Bild (wie bei LEGO Art): Er verbindet die Platten
    zusätzlich und gibt beim Legen der Abschnitte Halt.
    """
    steps: list[BuildStep] = []
    if mosaic.substrate:
        steps.append(BuildStep(StepKind.SUBSTRATE, mosaic.substrate))
    if mosaic.connectors:
        steps.append(BuildStep(StepKind.CONNECTORS, mosaic.connectors))
    if mosaic.bond:
        steps.append(BuildStep(StepKind.BOND, _by_color(mosaic.bond)))
    # groupby nach Höhe: jede Lage des Rahmen-Unterbaus wird ein eigener Schritt
    by_height = sorted(mosaic.supports, key=lambda p: p.bottom_ldu)
    for level, (_, group) in enumerate(groupby(by_height, key=lambda p: p.bottom_ldu)):
        steps.append(BuildStep(StepKind.FRAME_SUPPORT, _by_color(group), level=level))
    if mosaic.frame:
        steps.append(BuildStep(StepKind.FRAME, _by_color(mosaic.frame)))
    steps += build_steps(
        mosaic.placements,
        width=mosaic.width,
        height=mosaic.height,
        section_studs=section_studs,
        rows_per_step=rows_per_step,
        relief=mosaic.relief,
    )
    return tuple(steps)


def _by_color(placements: Iterable[Placement]) -> tuple[Placement, ...]:
    """Sortiert nach Farbe, dann Position (so verbaut man eine Farbe nach der anderen)."""
    return tuple(sorted(placements, key=lambda p: (p.color.id, p.row, p.col)))


def build_steps(
    placements: Sequence[Placement],
    *,
    width: int,
    height: int,
    section_studs: int,
    rows_per_step: int,
    relief: Sequence[Placement] = (),
) -> tuple[BuildStep, ...]:
    """Teilt die Bildteile in Bauschritte: erst nach Abschnitt, darin nach Zeilenblöcken.

    Ein Teil gehört zum Abschnitt und Zeilenblock seiner Startzelle. Relief-Stützteile
    kommen in jedem Abschnitt zuerst, eine Lage pro Schritt (von unten nach oben).
    Innerhalb eines Schritts wird nach Farbe, dann Position sortiert, damit man eine
    Farbe nach der anderen verbauen kann. Leere Schritte entfallen.
    """
    sections = split_sections(width, height, section_studs)

    # Schlüssel (Abschnitt, Phase, Nummer): Phase 0 = Relief-Lage, 1 = Zeilenblock des Bilds
    def relief_key(placement: Placement) -> tuple[int, int, int]:
        index = section_index(placement.row, placement.col, width, section_studs)
        return index, 0, placement.bottom_ldu // PLATE_HEIGHT_LDU

    def image_key(placement: Placement) -> tuple[int, int, int]:
        index = section_index(placement.row, placement.col, width, section_studs)
        # % ist der Rest der Division: Zeile innerhalb des Abschnitts; // teilt in Blöcke
        return index, 1, (placement.row % section_studs) // rows_per_step

    keyed = [(relief_key(p), p) for p in relief] + [(image_key(p), p) for p in placements]
    keyed.sort(key=lambda item: (*item[0], item[1].color.id, item[1].row, item[1].col))
    # groupby fasst aufeinanderfolgende Elemente mit gleichem Schlüssel zusammen
    # (funktioniert nur, weil vorher nach demselben Schlüssel sortiert wurde)
    steps = []
    for (index, phase, number), group in groupby(keyed, key=lambda item: item[0]):
        parts = tuple(placement for _, placement in group)
        if phase == 0:
            steps.append(BuildStep(StepKind.RELIEF, parts, section=sections[index], level=number))
        else:
            steps.append(BuildStep(StepKind.SECTION, parts, section=sections[index], block=number))
    return tuple(steps)
