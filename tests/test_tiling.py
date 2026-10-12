"""Tests für die Zerlegungsstrategien."""

import dataclasses
from collections.abc import Sequence

import numpy as np
import pytest

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.errors import TilingError
from lego_mosaik.domain.models import (
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    Placement,
    TilingStats,
)
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.registry import TILING_STRATEGIES
from lego_mosaik.tiling.greedy import GreedyTiling

STRATEGIES = sorted(TILING_STRATEGIES)


@pytest.fixture
def palette(catalog: PartCatalog) -> tuple[LegoColor, ...]:
    """Black(0), Blue(1), Red(2), White(3) – Indizes in dieser Reihenfolge."""
    return build_palette(catalog, (PartKind.PLATE,))


def _assert_valid_tiling(
    placements: Sequence[Placement],
    grid: IndexGrid,
    palette: Sequence[LegoColor],
    catalog: PartCatalog,
) -> None:
    """Prüft: lückenlos, ohne Überlappung, farbrichtig, jedes Teil verfügbar."""
    coverage = np.zeros(grid.shape, dtype=int)
    for placement in placements:
        assert catalog.is_available(placement.part.part_num, placement.color.id)
        for row, col in placement.cells():
            coverage[row, col] += 1
            assert palette[grid[row, col]] == placement.color
    assert sum(p.part.area for p in placements) == grid.size  # Flächensumme = B × H
    assert (coverage == 1).all()  # jede Zelle genau einmal belegt


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_random_grid_is_tiled_completely(
    strategy: str,
    app_config: AppConfig,
    catalog: PartCatalog,
    palette: tuple[LegoColor, ...],
) -> None:
    config = dataclasses.replace(
        app_config, palette=dataclasses.replace(app_config.palette, part_kinds=(PartKind.PLATE,))
    )
    rng = np.random.default_rng(seed=7)
    # wenige Farben → größere zusammenhängende Flächen
    grid = rng.choice([0, 3], size=(12, 16), p=[0.3, 0.7]).astype(np.intp)
    placements = TILING_STRATEGIES[strategy](config).tile(grid, palette, catalog)
    _assert_valid_tiling(placements, grid, palette, catalog)


def test_4x4_area_with_2x4_gives_two_parts(
    catalog: PartCatalog, palette: tuple[LegoColor, ...]
) -> None:
    grid = np.zeros((4, 4), dtype=np.intp)  # alles Black, 2×4 in Black verfügbar
    placements = GreedyTiling((PartKind.PLATE,), max_part_length=8).tile(grid, palette, catalog)
    assert len(placements) == 2
    assert {p.part.part_num for p in placements} == {"3020"}


def test_vertical_strip_uses_rotated_part(
    catalog: PartCatalog, palette: tuple[LegoColor, ...]
) -> None:
    grid = np.full((4, 3), 2, dtype=np.intp)  # Red (nur 1×1 und 1×2 verfügbar)
    grid[:, 0] = 0  # linke Spalte Black: 4 hoch, 1 breit → 1×4 gedreht
    placements = GreedyTiling((PartKind.PLATE,), max_part_length=8).tile(grid, palette, catalog)
    first = placements[0]
    assert (first.part.part_num, first.rotated) == ("3710", True)
    assert (first.rows_spanned, first.cols_spanned) == (4, 1)
    _assert_valid_tiling(placements, grid, palette, catalog)


def test_color_without_larger_parts_uses_only_single_studs(
    catalog: PartCatalog, palette: tuple[LegoColor, ...]
) -> None:
    grid = np.full((3, 3), 1, dtype=np.intp)  # Blue gibt es nur als 1×1
    placements = GreedyTiling((PartKind.PLATE,), max_part_length=8).tile(grid, palette, catalog)
    assert {p.part.part_num for p in placements} == {"3024"}
    assert len(placements) == 9


def test_max_part_length_limits_parts(catalog: PartCatalog, palette: tuple[LegoColor, ...]) -> None:
    grid = np.zeros((4, 4), dtype=np.intp)
    placements = GreedyTiling((PartKind.PLATE,), max_part_length=2).tile(grid, palette, catalog)
    assert max(p.part.length_studs for p in placements) <= 2
    _assert_valid_tiling(placements, grid, palette, catalog)


def test_kind_priority_decides_between_equal_sizes(catalog: PartCatalog) -> None:
    white = catalog.colors[15]  # 1×1 als Plate (3024) und als Tile (3070b) verfügbar
    grid = np.zeros((1, 1), dtype=np.intp)
    tile_first = GreedyTiling((PartKind.TILE, PartKind.PLATE), 8).tile(grid, [white], catalog)
    plate_first = GreedyTiling((PartKind.PLATE, PartKind.TILE), 8).tile(grid, [white], catalog)
    assert tile_first[0].part.part_num == "3070b"
    assert plate_first[0].part.part_num == "3024"


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mixing_bricks_with_plates_is_rejected(
    strategy: str, app_config: AppConfig, catalog: PartCatalog
) -> None:
    config = dataclasses.replace(
        app_config,
        palette=dataclasses.replace(
            app_config.palette, part_kinds=(PartKind.PLATE, PartKind.BRICK)
        ),
    )
    grid = np.zeros((2, 2), dtype=np.intp)
    with pytest.raises(TilingError, match="gleicher Höhe"):
        TILING_STRATEGIES[strategy](config).tile(grid, [catalog.colors[15]], catalog)


def test_tiling_stats() -> None:
    stats = TilingStats.from_placements(4, 4, [object()] * 2)  # type: ignore[list-item]
    assert (stats.pixels, stats.parts, stats.savings_percent) == (16, 2, 87.5)
