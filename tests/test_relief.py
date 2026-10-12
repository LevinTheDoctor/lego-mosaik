"""Tests für das Relief (domain/relief.py)."""

import numpy as np
import pytest

from lego_mosaik.domain.models import EMPTY_CELL, LegoColor, PartCatalog, ReliefMode
from lego_mosaik.domain.relief import background_mask, relief_levels


@pytest.fixture
def black_white(catalog: PartCatalog) -> tuple[LegoColor, ...]:
    return catalog.colors[0], catalog.colors[15]


def test_subject_raises_everything_but_the_border_background(
    black_white: tuple[LegoColor, ...],
) -> None:
    # Weißer Hintergrund (1) mit schwarzem Ring (0); das Weiß im Ring gehört zum Motiv
    grid = np.ones((7, 7), dtype=np.intp)
    grid[1:6, 1:6] = 0
    grid[3, 3] = 1
    levels = relief_levels(grid, black_white, ReliefMode.SUBJECT, 2)
    assert levels[0, 0] == 0 and levels[6, 6] == 0  # Hintergrund bleibt flach
    assert levels[1, 1] == 2  # Motiv
    assert levels[3, 3] == 2  # gleiche Farbe wie der Hintergrund, aber eingeschlossen


def test_background_ignores_empty_frame_cells() -> None:
    grid = np.full((5, 5), EMPTY_CELL, dtype=np.intp)
    grid[1:4, 1:4] = 0
    grid[2, 2] = 1
    mask = background_mask(grid)
    assert mask[1, 1] and not mask[2, 2] and not mask[0, 0]


def test_bright_and_dark(black_white: tuple[LegoColor, ...]) -> None:
    grid = np.array([[0, 0, 1, 1]], dtype=np.intp)
    bright = relief_levels(grid, black_white, ReliefMode.BRIGHT, 1)
    dark = relief_levels(grid, black_white, ReliefMode.DARK, 1)
    assert bright.tolist() == [[0, 0, 1, 1]]  # Weiß liegt höher
    assert dark.tolist() == [[1, 1, 0, 0]]


def test_none_and_empty_cells_stay_flat(black_white: tuple[LegoColor, ...]) -> None:
    grid = np.array([[EMPTY_CELL, 0, 1]], dtype=np.intp)
    assert relief_levels(grid, black_white, ReliefMode.NONE, 3).tolist() == [[0, 0, 0]]
    assert relief_levels(grid, black_white, ReliefMode.BRIGHT, 1)[0, 0] == 0
