"""Tests für die Umrisse der Formteile (domain/shapes.py)."""

import math

import pytest

from lego_mosaik.domain.models import PartShape
from lego_mosaik.domain.shapes import (
    cell_outline,
    coverage,
    outline,
    polygon_signed_area,
    shape_size,
    shape_turns,
    stud_visible,
)

SUBSAMPLE = 8


def test_quarter_turn_moves_right_angle_clockwise() -> None:
    # Grundlage wie 25269.dat: rechter Winkel unten links; eine Drehung → oben links
    assert outline(PartShape.QUARTER, 0)[0] == (0.0, 1.0)
    assert outline(PartShape.QUARTER, 1)[0] == (0.0, 0.0)
    assert outline(PartShape.QUARTER, 2)[0] == (1.0, 0.0)
    assert outline(PartShape.QUARTER, 4) == outline(PartShape.QUARTER, 0)


@pytest.mark.parametrize(
    ("shape", "area"),
    [
        (PartShape.QUARTER, math.pi / 4),
        (PartShape.HALF, 0.5 + math.pi / 8),
        (PartShape.ROUND, math.pi / 4),
        (PartShape.CUT_CORNER, 3.5),
    ],
)
def test_outline_area(shape: PartShape, area: float) -> None:
    assert abs(polygon_signed_area(outline(shape))) == pytest.approx(area, abs=0.01)


@pytest.mark.parametrize("shape", [s for s in PartShape if s is not PartShape.SQUARE])
@pytest.mark.parametrize("turns", range(4))
def test_coverage_matches_outline_area(shape: PartShape, turns: int) -> None:
    mask = coverage(shape, turns, SUBSAMPLE)
    size = shape_size(shape)
    assert mask.shape == (size * SUBSAMPLE, size * SUBSAMPLE)
    covered = mask.sum() / SUBSAMPLE**2  # in Noppen
    # Abtastfehler an schrägen Kanten: wenige Hundertstel Noppen
    assert covered == pytest.approx(abs(polygon_signed_area(outline(shape, turns))), abs=0.03)


def test_triangle_leaves_far_cell_free() -> None:
    # Dreieck mit rechtem Winkel oben links: die Zelle unten rechts bleibt frei
    assert cell_outline(PartShape.TRIANGLE, 0, 1, 1) is None
    top_left = cell_outline(PartShape.TRIANGLE, 0, 0, 0)
    assert top_left is not None
    assert abs(polygon_signed_area(top_left)) == pytest.approx(1.0)
    diagonal = cell_outline(PartShape.TRIANGLE, 0, 0, 1)
    assert diagonal is not None and 0.3 < abs(polygon_signed_area(diagonal)) < 0.6


def test_cut_corner_plate_has_three_studs() -> None:
    studs = {
        (row, col)
        for row in range(2)
        for col in range(2)
        if stud_visible(PartShape.CUT_CORNER, 0, row, col)
    }
    assert studs == {(0, 0), (1, 0), (1, 1)}  # die schräge Ecke oben rechts hat keine Noppe
    turned = {
        (r, c) for r in range(2) for c in range(2) if stud_visible(PartShape.CUT_CORNER, 1, r, c)
    }
    assert (1, 1) not in turned  # nach einer Drehung liegt die Schräge unten rechts


def test_round_plate_has_a_stud() -> None:
    assert stud_visible(PartShape.ROUND, 0, 0, 0)


def test_symmetric_shapes_need_one_turn() -> None:
    assert list(shape_turns(PartShape.ROUND)) == [0]
    assert list(shape_turns(PartShape.QUARTER)) == [0, 1, 2, 3]
