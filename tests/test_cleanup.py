"""Tests für das Aufräumen nach dem Dithering (domain/cleanup.py)."""

import numpy as np

from lego_mosaik.domain.cleanup import (
    majority_colors,
    majority_filter,
    merge_small_regions,
    rare_colors,
    remove_isolated,
)
from lego_mosaik.domain.models import EMPTY_CELL

# Lab-Werte: 0 = Weiß, 1 = Schwarz, 2 = Rot, 3 = Hellgrau (Weiß am ähnlichsten)
PALETTE_LAB = np.array([[100.0, 0, 0], [0.0, 0, 0], [53.0, 80, 67], [80.0, 0, 0]])
# Farbabstände dazu (euklidisch genügt für die Tests): Weiß–Hellgrau 20, Weiß–Schwarz 100
DISTANCES = np.linalg.norm(PALETTE_LAB[:, np.newaxis] - PALETTE_LAB[np.newaxis], axis=-1)
WHITE, BLACK, RED, LIGHT_GRAY = 0, 1, 2, 3


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


def test_majority_color_wins_over_edge_pixels() -> None:
    # 2 × 1 Noppen à 4 × 4 Teilpixel: links 10 Weiß + 6 Schwarz, rechts nur Rot
    sub = np.full((4, 8), WHITE, dtype=np.intp)
    sub[:, 3] = BLACK
    sub[:2, 2] = BLACK
    sub[:, 4:] = RED
    assert majority_colors(sub, 4, 4).tolist() == [[WHITE, RED]]


def test_small_similar_speckles_are_merged() -> None:
    grid = np.full((6, 6), WHITE, dtype=np.intp)
    grid[2, 2:4] = LIGHT_GRAY  # 2 Zellen Hellgrau im Weiß (ΔE 20)
    result = merge_small_regions(grid, DISTANCES, min_cells=3, max_delta_e=25)
    assert (result == WHITE).all()


def test_eyes_with_high_contrast_stay() -> None:
    # Ein Auge im schwarzen Kopf: Weiß mit schwarzer Pupille in der Mitte
    grid = np.full((7, 7), BLACK, dtype=np.intp)
    grid[2:5, 2:5] = WHITE
    grid[3, 3] = BLACK
    result = merge_small_regions(grid, DISTANCES, min_cells=9, max_delta_e=40)
    assert np.array_equal(result, grid)


def test_large_regions_and_empty_cells_stay() -> None:
    grid = np.full((5, 5), WHITE, dtype=np.intp)
    grid[0:2, 0:2] = LIGHT_GRAY  # 4 Zellen: nicht klein genug
    grid[4, 4] = EMPTY_CELL
    assert np.array_equal(merge_small_regions(grid, DISTANCES, 4, 25), grid)


def test_majority_filter_smooths_a_seam_but_keeps_lines() -> None:
    grid = np.full((7, 7), WHITE, dtype=np.intp)
    grid[3, :] = BLACK  # Umriss, eine Noppe breit
    grid[1, 1:3] = LIGHT_GRAY  # Saum im Weiß
    result = majority_filter(grid, DISTANCES, need=6, max_delta_e=40)
    assert result[3].tolist() == [BLACK] * 7
    assert result[1, 1] == WHITE and result[1, 2] == WHITE
