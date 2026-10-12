"""Tests für Grundplatten-Layout und Rahmen."""

import numpy as np
import pytest

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import (
    apply_frame,
    baseplate_placements,
    find_baseplate,
    layout_size,
    require_baseplate_color,
)
from lego_mosaik.domain.models import PartCatalog, PartKind
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.tiling.greedy import GreedyTiling


def test_layout_size_subtracts_frame() -> None:
    size = layout_size(baseplate_studs=32, plates_wide=2, plates_high=1, frame_width=1)
    assert (size.width, size.height, size.image_width, size.image_height) == (64, 32, 62, 30)


def test_find_baseplate(catalog: PartCatalog) -> None:
    assert find_baseplate(catalog, 16).part_num == "3867"
    with pytest.raises(ConfigError, match=r"Keine Grundplatte mit 32 × 32 Noppen\. Verfügbar: 16"):
        find_baseplate(catalog, 32)


def test_baseplate_color_must_exist(catalog: PartCatalog) -> None:
    baseplate = find_baseplate(catalog, 16)
    assert require_baseplate_color(catalog, baseplate, 15).name == "White"
    with pytest.raises(ConfigError, match="Verfügbar: 15 White"):
        require_baseplate_color(catalog, baseplate, 0)


def test_baseplates_cover_layout_exactly(catalog: PartCatalog) -> None:
    baseplate = find_baseplate(catalog, 16)
    plates = baseplate_placements(baseplate, catalog.colors[15], plates_wide=3, plates_high=2)
    coverage = np.zeros((32, 48), dtype=int)
    for plate in plates:
        for row, col in plate.cells():
            coverage[row, col] += 1
    assert (coverage == 1).all()
    assert [(p.row, p.col) for p in plates[:4]] == [(0, 0), (0, 16), (0, 32), (16, 0)]


def test_frame_adds_new_color_at_end(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))[1:]  # ohne Black: Blue, Red, White
    grid = np.zeros((2, 3), dtype=np.intp)
    framed, new_palette = apply_frame(grid, palette, 1, catalog.colors[0])
    assert framed.shape == (4, 5)
    assert new_palette[:3] == palette and new_palette[3].name == "Black"
    border = np.concatenate([framed[0], framed[-1], framed[:, 0], framed[:, -1]])
    assert set(border.tolist()) == {3}
    assert (framed[1:-1, 1:-1] == grid).all()  # Innenfläche unverändert


def test_frame_reuses_existing_color(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))
    framed, new_palette = apply_frame(np.full((2, 2), 3, dtype=np.intp), palette, 2, palette[0])
    assert new_palette == palette
    assert framed[0, 0] == 0 and framed.shape == (6, 6)


def test_no_frame_keeps_grid(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))
    grid = np.zeros((2, 2), dtype=np.intp)
    framed, new_palette = apply_frame(grid, palette, 0, palette[0])
    assert framed is grid and new_palette == palette


def test_frame_is_tiled_with_long_parts(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))
    framed, palette = apply_frame(np.full((6, 6), 3, dtype=np.intp), palette, 1, palette[0])
    placements = GreedyTiling((PartKind.PLATE,), 8).tile(framed, palette, catalog)
    black = [p for p in placements if p.color.name == "Black"]
    assert sum(p.part.area for p in black) == 8 * 8 - 6 * 6  # Rahmen vollständig belegt
    assert max(p.part.length_studs for p in black) == 4  # 1×4 in Black verfügbar
