"""Tests für die Formwahl (Stile „formen“, „punkte“, „noppen“)."""

import numpy as np
import numpy.typing as npt
import pytest

from lego_mosaik.domain.models import (
    EMPTY_CELL,
    ColorDistance,
    LegoColor,
    PartCatalog,
    PartKind,
    PartShape,
)
from lego_mosaik.imaging.color_space import LabColorSpace
from lego_mosaik.shaping.contour import ContourShaper
from lego_mosaik.shaping.dots import DotShaper

S = 6  # Teilpixel pro Noppe
RED, WHITE, BLACK = (201, 26, 9), (255, 255, 255), (5, 19, 29)
SHAPES = (PartShape.QUARTER, PartShape.ROUND, PartShape.TRIANGLE)

type FloatImage = npt.NDArray[np.float64]


@pytest.fixture
def palette(catalog: PartCatalog) -> tuple[LegoColor, ...]:
    """Black(0), Red(1), White(2)."""
    return catalog.colors[0], catalog.colors[4], catalog.colors[15]


def _lab(rgb: npt.NDArray[np.float64]) -> FloatImage:
    return LabColorSpace(ColorDistance.CIE76).to_lab(rgb)


def _diagonal_image(studs: int) -> FloatImage:
    """Rot oben links bis zur Diagonale (x + y < studs), sonst Weiß, in Teilpixeln."""
    pixels = studs * S
    ys, xs = np.mgrid[0:pixels, 0:pixels] + 0.5
    image = np.where(((xs + ys) / S < studs)[..., None], RED, WHITE).astype(np.float64)
    return _lab(image)


def _shaper(*, underlay: bool) -> ContourShaper:
    return ContourShaper(SHAPES, subsample=S, min_gain=0.2, min_improvement=3.0, underlay=underlay)


def _refine(
    shaper: ContourShaper,
    detail: FloatImage,
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    grid: npt.NDArray[np.intp],
) -> object:
    palette_lab = _lab(np.array([c.rgb for c in palette], dtype=np.float64))
    background = np.broadcast_to(_lab(np.array([BLACK], dtype=np.float64))[0], (*grid.shape, 3))
    return shaper.refine(
        grid,
        detail,
        palette,
        palette_lab,
        catalog,
        levels=np.zeros(grid.shape, dtype=np.intp),
        background_lab=background,
    )


def test_diagonal_gets_triangles_on_white_underlays(
    palette: tuple[LegoColor, ...], catalog: PartCatalog
) -> None:
    grid = np.full((4, 4), 2, dtype=np.intp)
    grid[np.add.outer(range(4), range(4)) < 3] = 1  # Rot oberhalb der Diagonale
    result = _refine(_shaper(underlay=True), _diagonal_image(4), palette, catalog, grid)
    triangles = [p for p in result.parts if p.part.shape is PartShape.TRIANGLE]
    assert triangles, result
    # Gleichwertig: rotes Dreieck (Winkel oben links) auf Weiß oder weißes (gedreht) auf Rot
    expected_turns = {4: 0, 15: 2}
    assert all(p.turns == expected_turns[p.color.id] for p in triangles)
    assert all(p.bottom_ldu == 8 for p in triangles)  # liegen auf einer Unterlage
    under = {(p.row, p.col): p.color.id for p in result.underlays}
    for triangle in triangles:
        assert {triangle.color.id, under[triangle.row, triangle.col]} == {4, 15}
    assert not any(p.row == 0 and p.col == 0 and p.part.width_studs == 1 for p in result.parts)


def test_without_underlay_gaps_show_the_ground(
    palette: tuple[LegoColor, ...], catalog: PartCatalog
) -> None:
    grid = np.full((4, 4), 2, dtype=np.intp)
    grid[np.add.outer(range(4), range(4)) < 3] = 1
    result = _refine(_shaper(underlay=False), _diagonal_image(4), palette, catalog, grid)
    assert not result.underlays
    assert all(p.bottom_ldu == 0 for p in result.parts)


def test_flat_areas_and_empty_cells_stay_square(
    palette: tuple[LegoColor, ...], catalog: PartCatalog
) -> None:
    grid = np.full((4, 4), 1, dtype=np.intp)
    grid[0, :] = EMPTY_CELL
    detail = _lab(np.full((4 * S, 4 * S, 3), RED, dtype=np.float64))
    result = _refine(_shaper(underlay=True), detail, palette, catalog, grid)
    assert not result.parts and not result.underlays


def test_dots_cover_every_cell(palette: tuple[LegoColor, ...], catalog: PartCatalog) -> None:
    shaper = DotShaper(PartKind.TILE)
    grid = np.array([[0, 1], [2, EMPTY_CELL]], dtype=np.intp)
    result = shaper.refine(
        grid,
        None,
        palette,
        np.zeros((3, 3)),
        catalog,
        levels=np.zeros(grid.shape, dtype=np.intp),
        background_lab=np.zeros((2, 2, 3)),
    )
    assert sorted((p.row, p.col) for p in result.parts) == [(0, 0), (0, 1), (1, 0)]
    assert {p.part.part_num for p in result.parts} == {"98138"}


def test_dots_only_accept_colors_with_round_parts(catalog: PartCatalog) -> None:
    tiles, plates = DotShaper(PartKind.TILE), DotShaper(PartKind.PLATE)
    assert tiles.accepts(catalog.colors[0], catalog)  # Tile Round in Black
    assert not plates.accepts(catalog.colors[0], catalog)  # Plate Round nicht in Black
    assert not tiles.accepts(catalog.colors[1], catalog)  # Blue hat kein rundes Teil
