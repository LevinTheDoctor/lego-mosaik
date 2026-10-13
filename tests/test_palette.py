"""Tests für den Aufbau der Palette."""

import dataclasses
from collections.abc import Sequence

import numpy as np

from lego_mosaik.domain.models import (
    ColorCategory,
    LegoColor,
    PartCatalog,
    PartKind,
    color_family_key,
)
from lego_mosaik.domain.palette import build_palette, color_codes, most_used_colors


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


def test_color_codes_are_short_unique_and_follow_the_shown_name() -> None:
    names = ["Black", "Blue", "Dark Blue", "Dark Brown", "Light Bluish Gray", "Dark Bluish Gray"]
    colors = [LegoColor(i, name, (0, 0, 0), False, i) for i, name in enumerate(names)]
    codes = color_codes(colors)
    assert codes == {0: "Bla", 1: "Blu", 2: "DBl", 3: "DBr", 4: "LBG", 5: "DBG"}
    official = dataclasses.replace(colors[4], lego_name="Medium Stone Grey")
    assert color_codes([official])[4] == "MSG"  # Kürzel passt zum angezeigten LEGO-Namen


def test_color_family_puts_similar_colors_together() -> None:
    def color(color_id: int, rgb: tuple[int, int, int]) -> LegoColor:
        return LegoColor(color_id, str(color_id), rgb, False, color_id)

    white, gray, black = color(1, (255, 255, 255)), color(2, (160, 165, 169)), color(3, (5, 19, 29))
    red, blue = color(4, (201, 26, 9)), color(5, (0, 85, 191))
    light_blue = color(6, (159, 195, 233))
    ordered = sorted([blue, red, black, light_blue, white, gray], key=color_family_key)
    assert ordered == [white, gray, black, red, light_blue, blue]
