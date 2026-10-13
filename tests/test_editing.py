"""Tests für das Umfärben von Noppen und Flächen (``domain/editing.py``)."""

import numpy as np
import pytest

from lego_mosaik.domain.editing import (
    AreaEdit,
    FillEdit,
    StudEdit,
    apply_edits,
    connected,
    edit_from_json,
    edit_to_json,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import LegoColor

BLACK = LegoColor(0, "Black", (5, 19, 29), False, 0)
WHITE = LegoColor(15, "White", (255, 255, 255), False, 15)
RED = LegoColor(4, "Red", (201, 26, 9), False, 4)
ALLOWED = {color.id: color for color in (BLACK, WHITE, RED)}


def _grid() -> np.ndarray:
    """4 × 4: linke Hälfte Schwarz (0), rechte Hälfte Weiß (1)."""
    grid = np.zeros((4, 4), dtype=np.intp)
    grid[:, 2:] = 1
    return grid


def test_stud_edit_counts_rows_from_one_with_frame() -> None:
    grid, palette, changed = apply_edits(
        _grid(), (BLACK, WHITE), [StudEdit(2, 2, 4)], ALLOWED, frame_width=1
    )
    assert palette == (BLACK, WHITE, RED)  # Rot kam neu dazu
    assert grid[0, 0] == 2 and changed.sum() == 1


def test_area_and_fill_edits() -> None:
    grid, _, _ = apply_edits(
        _grid(), (BLACK, WHITE), [AreaEdit(1, 1, 2, 3, 15)], ALLOWED, frame_width=0
    )
    assert (grid[:3, :2] == 1).all() and (grid[3, :2] == 0).all()
    grid, _, changed = apply_edits(
        _grid(), (BLACK, WHITE), [FillEdit(1, 4, 0)], ALLOWED, frame_width=0
    )
    assert (grid == 0).all() and changed[:, 2:].all() and not changed[:, :2].any()


def test_edits_outside_the_image_or_with_unknown_colors_fail() -> None:
    with pytest.raises(ConfigError, match="nicht in der Bildfläche"):
        apply_edits(_grid(), (BLACK, WHITE), [StudEdit(1, 1, 0)], ALLOWED, frame_width=1)
    with pytest.raises(ConfigError, match="nicht in der Bildfläche"):
        apply_edits(_grid(), (BLACK, WHITE), [AreaEdit(3, 3, 3, 1, 0)], ALLOWED, frame_width=0)
    with pytest.raises(ConfigError, match="1×1-Teil"):
        apply_edits(_grid(), (BLACK, WHITE), [StudEdit(1, 1, 99)], ALLOWED, frame_width=0)


def test_connected_only_follows_four_neighbors() -> None:
    same = np.array([[True, False], [False, True]])
    assert connected(same, [(0, 0)]).sum() == 1


@pytest.mark.parametrize("edit", [StudEdit(1, 2, 3), AreaEdit(1, 2, 3, 4, 5), FillEdit(6, 7, 8)])
def test_json_round_trip(edit: StudEdit | AreaEdit | FillEdit) -> None:
    assert edit_from_json(edit_to_json(edit)) == edit


def test_invalid_json_edits_are_rejected() -> None:
    with pytest.raises(ConfigError, match="Unbekannte Art"):
        edit_from_json({"art": "kreis", "zeile": 1, "spalte": 1, "farbe_id": 0})
    with pytest.raises(ConfigError, match="Ungültige Änderung"):
        edit_from_json({"art": "noppe", "zeile": "1", "spalte": 1, "farbe_id": 0})
