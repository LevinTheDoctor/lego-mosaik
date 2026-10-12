"""Tests für die Umrisse der Formteile (domain/shapes.py)."""

import math

import pytest

from lego_mosaik.domain.models import PartCatalog, PartShape, Placement
from lego_mosaik.domain.shapes import (
    box,
    cell_outline,
    coverage,
    outline,
    parse_outline,
    part_turns,
    polygon_signed_area,
    stud_visible,
)

SUBSAMPLE = 8
SHAPED = ("98138", "25269", "35787", "43722")


def test_quarter_turn_moves_right_angle_clockwise(catalog: PartCatalog) -> None:
    quarter = catalog.part_types["25269"]
    # Grundlage wie 25269.dat: rechter Winkel unten links; eine Drehung → oben links
    assert outline(quarter, 0)[0] == (0.0, 1.0)
    assert outline(quarter, 1)[0] == (0.0, 0.0)
    assert outline(quarter, 2)[0] == (1.0, 0.0)
    assert outline(quarter, 4) == outline(quarter, 0)


@pytest.mark.parametrize(
    ("part_num", "area"),
    [("25269", math.pi / 4), ("98138", math.pi / 4), ("43722", 4.5)],
)
def test_outline_area(catalog: PartCatalog, part_num: str, area: float) -> None:
    part = catalog.part_types[part_num]
    assert abs(polygon_signed_area(outline(part))) == pytest.approx(area, abs=0.01)


@pytest.mark.parametrize("part_num", SHAPED)
@pytest.mark.parametrize("turns", range(4))
def test_coverage_matches_outline_area(catalog: PartCatalog, part_num: str, turns: int) -> None:
    part = catalog.part_types[part_num]
    rows, cols = box(part, turns)
    mask = coverage(part, turns, SUBSAMPLE)
    assert mask.shape == (rows * SUBSAMPLE, cols * SUBSAMPLE)
    covered = mask.sum() / SUBSAMPLE**2  # in Noppen
    # Abtastfehler an schrägen Kanten: wenige Hundertstel Noppen
    assert covered == pytest.approx(abs(polygon_signed_area(outline(part, turns))), abs=0.03)


def test_wedge_box_swaps_with_each_turn(catalog: PartCatalog) -> None:
    wedge = catalog.part_types["43722"]  # 3 × 2, in LDraw lang entlang z (hochkant)
    assert box(wedge, 0) == (3, 2) and box(wedge, 1) == (2, 3)
    flat = Placement(wedge, catalog.colors[4], 0, 0, turns=1)
    assert (flat.rows_spanned, flat.cols_spanned) == (2, 3)
    # Nach einer Drehung bleibt der Umriss in der neuen, liegenden Box
    xs = [x for x, _ in outline(wedge, 1)]
    ys = [y for _, y in outline(wedge, 1)]
    assert (min(xs), max(xs), min(ys), max(ys)) == (0, 3, 0, 2)


def test_triangle_leaves_far_cell_free(catalog: PartCatalog) -> None:
    triangle = catalog.part_types["35787"]
    # Dreieck mit rechtem Winkel oben links: die Zelle unten rechts bleibt frei
    assert cell_outline(triangle, 0, 1, 1) is None
    top_left = cell_outline(triangle, 0, 0, 0)
    assert top_left is not None
    assert abs(polygon_signed_area(top_left)) == pytest.approx(1.0)
    diagonal = cell_outline(triangle, 0, 0, 1)
    assert diagonal is not None and 0.3 < abs(polygon_signed_area(diagonal)) < 0.6


def test_wedge_plate_studs(catalog: PartCatalog) -> None:
    wedge = catalog.part_types["43722"]  # Schräge von unten links nach oben Mitte
    studs = {(r, c) for r in range(3) for c in range(2) if stud_visible(wedge, 0, r, c)}
    # die volle rechte Spalte und das breite Ende der Schräge tragen Noppen (wie 43722)
    assert studs == {(0, 1), (1, 1), (2, 1), (2, 0)}


def test_round_plate_has_a_stud(catalog: PartCatalog) -> None:
    assert stud_visible(catalog.part_types["6141"], 0, 0, 0)


def test_symmetric_shapes_need_one_turn(catalog: PartCatalog) -> None:
    assert list(part_turns(catalog.part_types["98138"])) == [0]
    assert list(part_turns(catalog.part_types["43722"])) == [0, 1, 2, 3]
    assert catalog.part_types["43722"].shape is PartShape.WEDGE


def test_parse_outline() -> None:
    assert parse_outline("0 0; 1 0;2 1 ") == ((0.0, 0.0), (1.0, 0.0), (2.0, 1.0))
    with pytest.raises(ValueError):
        parse_outline("0 0;1 0")
