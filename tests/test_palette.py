"""Tests für den Aufbau der Palette."""

import dataclasses
from collections.abc import Sequence

import numpy as np

from lego_mosaik.domain.models import ColorCategory, LegoColor, PartCatalog, PartKind
from lego_mosaik.domain.palette import build_palette, most_used_colors


def _ids(colors: Sequence[LegoColor]) -> list[int]:
    return [color.id for color in colors]


def test_palette_requires_single_stud_part(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))
    # Green (2) hat nur ein 1×2-Teil, Yellow (14) nur ein Tile, Trans-Red (36) ist transparent
    assert _ids(palette) == [0, 1, 4, 15]


def test_palette_includes_tile_only_color_when_tiles_allowed(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE, PartKind.TILE))
    assert 14 in _ids(palette)


def test_palette_can_include_transparent_colors(catalog: PartCatalog) -> None:
    categories = (ColorCategory.SOLID, ColorCategory.TRANSPARENT)
    palette = build_palette(catalog, (PartKind.PLATE,), categories=categories)
    assert 36 in _ids(palette)


def test_include_ids_bypass_category(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,), include_color_ids=(36,))
    assert 36 in _ids(palette)


def test_exclude_wins_over_include(catalog: PartCatalog) -> None:
    palette = build_palette(
        catalog, (PartKind.PLATE,), include_color_ids=(36,), exclude_color_ids=(36,)
    )
    assert 36 not in _ids(palette)


def test_colors_without_ldraw_code_are_skipped(catalog: PartCatalog) -> None:
    colors = dict(catalog.colors)
    colors[4] = dataclasses.replace(colors[4], ldraw_code=None)
    palette = build_palette(dataclasses.replace(catalog, colors=colors), (PartKind.PLATE,))
    assert 4 not in _ids(palette)


def test_palette_respects_excluded_ids(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,), exclude_color_ids=(4,))
    assert 4 not in _ids(palette)


def test_most_used_colors_breaks_ties_by_id(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))  # Black, Blue, Red, White
    grid = np.array([[3, 3, 3], [1, 2, 0]], dtype=np.intp)
    # White kommt dreimal vor; Black, Blue, Red je einmal → Gleichstand, kleinste ID gewinnt
    assert _ids(most_used_colors(grid, palette, 2)) == [0, 15]
