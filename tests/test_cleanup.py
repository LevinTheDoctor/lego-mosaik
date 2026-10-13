"""Tests für das Aufräumen nach dem Dithering (domain/cleanup.py)."""

import numpy as np

from lego_mosaik.domain.cleanup import rare_colors, remove_isolated
from lego_mosaik.domain.models import EMPTY_CELL

# Lab-Werte: 0 = Weiß, 1 = Schwarz, 2 = Rot, 3 = Hellgrau (Weiß am ähnlichsten)
PALETTE_LAB = np.array([[100.0, 0, 0], [0.0, 0, 0], [53.0, 80, 67], [80.0, 0, 0]])


def test_rare_colors_counts_cells_and_ignores_empty() -> None:
    grid = np.array([[0, 0, 1], [0, 2, EMPTY_CELL]], dtype=np.intp)
    assert rare_colors(grid, 4, 2) == [1, 2, 3]  # 3 kommt gar nicht vor
    assert rare_colors(grid, 4, 1) == [3]


def test_isolated_pixel_takes_the_neighbor_color() -> None:
    grid = np.zeros((5, 5), dtype=np.intp)
    grid[2, 2] = 2
    assert remove_isolated(grid, PALETTE_LAB).tolist() == np.zeros((5, 5)).tolist()


def test_lines_stay() -> None:
    # Ein schwarzer Umriss, eine Noppe breit: jede Zelle hat schwarze Nachbarn
    grid = np.zeros((5, 5), dtype=np.intp)
    grid[2, :] = 1
    grid[0, 4] = 1  # zwei Zeilen über der Linie: ohne schwarzen Nachbarn
    result = remove_isolated(grid, PALETTE_LAB)
    assert result[2].tolist() == [1] * 5
    assert result[0, 4] == 0


def test_tie_goes_to_the_most_similar_color() -> None:
    # Ein rotes Pixel zwischen je vier schwarzen und hellgrauen Nachbarn
    grid = np.array([[1, 1, 3], [1, 2, 3], [1, 3, 3]], dtype=np.intp)
    result = remove_isolated(grid, PALETTE_LAB)
    # Abstand von Rot zu Schwarz ≈ 117, zu Hellgrau ≈ 108 → Hellgrau
    assert result[1, 1] == 3


def test_empty_cells_are_not_neighbors() -> None:
    grid = np.full((3, 3), EMPTY_CELL, dtype=np.intp)
    grid[1, 1] = 2
    assert remove_isolated(grid, PALETTE_LAB)[1, 1] == 2  # keine Nachbarn → bleibt
