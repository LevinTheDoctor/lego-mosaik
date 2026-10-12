"""Greedy-Zerlegung: an jeder freien Stelle das größte passende Teil legen.

"Greedy" (gierig) heißt: Jede Entscheidung ist lokal die beste und wird nie
zurückgenommen. Das findet nicht immer die minimale Teilezahl, ist aber schnell,
einfach nachvollziehbar und deterministisch.
"""

from collections.abc import Sequence
from dataclasses import dataclass

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.domain.errors import TilingError
from lego_mosaik.domain.models import (
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    PartType,
    Placement,
)
from lego_mosaik.tiling.common import validate_uniform_height


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Ein Teil in einer bestimmten Ausrichtung, bereit zum Platzieren."""

    part: PartType
    rotated: bool

    @property
    def rows(self) -> int:
        return self.part.length_studs if self.rotated else self.part.width_studs

    @property
    def cols(self) -> int:
        return self.part.width_studs if self.rotated else self.part.length_studs


class GreedyTiling:
    """Fasst gleichfarbige Pixel gierig zu möglichst großen Teilen zusammen.

    Args:
        part_kinds: Erlaubte Teilarten in Prioritätsreihenfolge.
        max_part_length: Längste erlaubte Teilseite in Noppen.
    """

    def __init__(self, part_kinds: Sequence[PartKind], max_part_length: int) -> None:
        self._part_kinds = tuple(part_kinds)
        self._max_part_length = max_part_length

    def tile(
        self, color_grid: IndexGrid, palette: Sequence[LegoColor], catalog: PartCatalog
    ) -> list[Placement]:
        """Zerlegt das Raster zeilenweise von oben links nach unten rechts."""
        validate_uniform_height(catalog, self._part_kinds)
        candidates = [self._candidates(catalog, color) for color in palette]
        occupied = np.zeros(color_grid.shape, dtype=bool)
        placements: list[Placement] = []
        height, width = color_grid.shape
        for row in range(height):
            for col in range(width):
                if not occupied[row, col]:
                    index = int(color_grid[row, col])
                    candidate = _first_fit(candidates[index], color_grid, occupied, row, col)
                    _mark(occupied, row, col, candidate)
                    placements.append(
                        Placement(candidate.part, palette[index], row, col, candidate.rotated)
                    )
        return placements

    def _candidates(self, catalog: PartCatalog, color: LegoColor) -> list[_Candidate]:
        """Alle verfügbaren Teile der Farbe in beiden Ausrichtungen, sortiert nach Priorität."""
        parts = [
            part
            for part in catalog.parts_in_color(color.id, self._part_kinds)
            if part.length_studs <= self._max_part_length
        ]
        # Quadratische Teile (z. B. 2×2) gibt es nur einmal, rechteckige in beiden Ausrichtungen
        candidates = [
            _Candidate(part, rotated)
            for part in parts
            for rotated in ((False,) if part.width_studs == part.length_studs else (False, True))
        ]
        return sorted(candidates, key=self._priority)

    def _priority(self, candidate: _Candidate) -> tuple[int, int, int, bool, str]:
        """Sortierschlüssel: größte Fläche, längste Seite, Teilart-Priorität, dann eindeutig.

        Negative Werte (-fläche) sortieren aufsteigend = groß zuerst. ``False < True``,
        also kommt die ungedrehte Ausrichtung vor der gedrehten.
        """
        part = candidate.part
        kind_rank = self._part_kinds.index(part.kind)
        return (-part.area, -part.length_studs, kind_rank, candidate.rotated, part.part_num)


def _first_fit(
    candidates: Sequence[_Candidate],
    color_grid: IndexGrid,
    occupied: npt.NDArray[np.bool_],
    row: int,
    col: int,
) -> _Candidate:
    """Der erste Kandidat, der an (row, col) passt.

    Passt heißt: im Raster, alle Zellen gleiche Farbe, alle noch frei.

    Raises:
        TilingError: Wenn nichts passt (kann nur ohne 1×1-Teil in der Farbe passieren).
    """
    height, width = color_grid.shape
    color_index = color_grid[row, col]
    for candidate in candidates:
        bottom, right = row + candidate.rows, col + candidate.cols
        if bottom > height or right > width:
            continue
        # Slicing [row:bottom, col:right] wählt das Rechteck unter dem Teil aus
        same_color = bool((color_grid[row:bottom, col:right] == color_index).all())
        if same_color and not occupied[row:bottom, col:right].any():
            return candidate
    raise TilingError(f"An Zeile {row}, Spalte {col} passt kein verfügbares Teil.")


def _mark(occupied: npt.NDArray[np.bool_], row: int, col: int, candidate: _Candidate) -> None:
    """Markiert die vom Teil belegten Zellen als besetzt (Zuweisung an ein Slice)."""
    occupied[row : row + candidate.rows, col : col + candidate.cols] = True
