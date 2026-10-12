"""Tests für Größe, Unterbau (Grundplatten, Art-Paneele, Pins) und Rahmen."""

import numpy as np
import pytest

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import (
    apply_frame,
    check_size,
    complete_size,
    connector_placements,
    corner_mask,
    custom_layout_size,
    find_connector,
    find_substrate_part,
    frame_mask,
    grid_layout_size,
    pin_offsets,
    require_part_color,
    running_bond_offset,
    substrate_placements,
)
from lego_mosaik.domain.models import PartCatalog, PartKind
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.tiling.greedy import GreedyTiling


def test_grid_layout_size_subtracts_frame() -> None:
    size = grid_layout_size(plate_studs=32, plates_wide=2, plates_high=1, frame_width=1)
    assert (size.width, size.height, size.image_width, size.image_height) == (64, 32, 62, 30)
    assert (size.plates_wide, size.plates_high) == (2, 1)


def test_custom_size_rounds_plates_up() -> None:
    size = custom_layout_size(width=40, height=16, plate_studs=16, frame_width=2)
    assert (size.width, size.height, size.image_width, size.image_height) == (40, 16, 36, 12)
    assert (size.plates_wide, size.plates_high) == (3, 1)  # 40 / 16 = 2,5 → 3 Paneele


def test_complete_size_keeps_aspect_ratio_of_image_area() -> None:
    # Bildfläche 2:1 quer; Breite 42 mit Rahmen 1 → Bildbreite 40 → Bildhöhe 20 → Höhe 22
    assert complete_size(42, None, 2.0, 1) == (42, 22)
    assert complete_size(None, 22, 2.0, 1) == (42, 22)
    assert complete_size(30, 20, 2.0, 1) == (30, 20)  # beide angegeben: unverändert
    with pytest.raises(ConfigError, match="Breite, Höhe oder beides"):
        complete_size(None, None, 1.0, 0)


def test_check_size_limits() -> None:
    with pytest.raises(ConfigError, match="höchstens 384 Noppen pro Seite"):
        check_size(400, 32, 0)
    with pytest.raises(ConfigError, match="mindestens 8"):
        check_size(4, 32, 0)
    with pytest.raises(ConfigError, match="Rahmen ist zu breit"):
        check_size(16, 16, 7)


def test_find_substrate_parts(catalog: PartCatalog) -> None:
    assert find_substrate_part(catalog, PartKind.BASEPLATE, 16).part_num == "3867"
    assert find_substrate_part(catalog, PartKind.PANEL, 16).part_num == "65803"
    with pytest.raises(ConfigError, match=r"keine Grundplatte mit 32 × 32 Noppen\. Verfügbar: 16"):
        find_substrate_part(catalog, PartKind.BASEPLATE, 32)
    with pytest.raises(ConfigError, match="kein LEGO-Art-Paneel mit 48 × 48"):
        find_substrate_part(catalog, PartKind.PANEL, 48)
    assert find_connector(catalog).part_num == "2780"


def test_part_color_must_exist(catalog: PartCatalog) -> None:
    baseplate = find_substrate_part(catalog, PartKind.BASEPLATE, 16)
    assert require_part_color(catalog, baseplate, 15).name == "White"
    with pytest.raises(ConfigError, match="Verfügbar: 15 White"):
        require_part_color(catalog, baseplate, 0)


def test_substrate_covers_layout_exactly(catalog: PartCatalog) -> None:
    baseplate = find_substrate_part(catalog, PartKind.BASEPLATE, 16)
    plates = substrate_placements(baseplate, catalog.colors[15], plates_wide=3, plates_high=2)
    coverage = np.zeros((32, 48), dtype=int)
    for plate in plates:
        for row, col in plate.cells():
            coverage[row, col] += 1
    assert (coverage == 1).all()
    assert [(p.row, p.col) for p in plates[:4]] == [(0, 0), (0, 16), (0, 32), (16, 0)]


def test_pin_offsets_are_spread_evenly() -> None:
    assert pin_offsets(16, 1) == (8,)
    assert pin_offsets(16, 2) == (4, 12)
    assert pin_offsets(16, 4) == (2, 6, 10, 14)


def test_pins_bridge_every_seam(catalog: PartCatalog) -> None:
    pin = find_connector(catalog)
    size = grid_layout_size(16, plates_wide=2, plates_high=2, frame_width=1)
    pins = connector_placements(pin, catalog.colors[0], size, 16, pins_per_seam=2)
    assert len(pins) == 4 * 2  # 2 × 2 Paneele haben 4 Nähte
    for placement in pins:
        cells = list(placement.cells())
        panels = {(row // 16, col // 16) for row, col in cells}
        assert len(cells) == 2 and len(panels) == 2  # je eine Hälfte in zwei Paneelen
    assert sum(p.rotated for p in pins) == 4  # waagerechte Nähte: gedrehte Pins


def test_running_bond_offset_never_puts_a_joint_on_a_seam() -> None:
    assert running_bond_offset(16, 8) == 4  # halbe Teillänge wie bei Ziegeln
    assert running_bond_offset(16, 4) == 2
    assert running_bond_offset(16, 1) == 0  # 1×1 überbrückt nichts
    for length in range(2, 9):
        offset = running_bond_offset(16, length)
        assert (16 - offset) % length != 0  # Naht liegt mitten in einem Teil


def test_frame_and_corner_masks() -> None:
    ring, corners = frame_mask(6, 8, 2), corner_mask(6, 8, 2)
    assert ring.sum() == 6 * 8 - 2 * 4  # außen minus Innenfläche
    assert corners.sum() == 4 * 2 * 2
    assert not (corners & ~ring).any()  # Ecken liegen im Rahmen


def test_frame_adds_new_color_at_end(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))[1:]  # ohne Black: Blue, Red, White
    grid = np.zeros((2, 3), dtype=np.intp)
    framed, new_palette = apply_frame(grid, palette, 1, catalog.colors[0])
    assert framed.shape == (4, 5)
    assert new_palette[:3] == palette and new_palette[3].name == "Black"
    border = np.concatenate([framed[0], framed[-1], framed[:, 0], framed[:, -1]])
    assert set(border.tolist()) == {3}
    assert (framed[1:-1, 1:-1] == grid).all()  # Innenfläche unverändert


def test_frame_corners_get_own_color(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))  # Black, Blue, Red, White
    grid = np.full((4, 4), 2, dtype=np.intp)
    framed, new_palette = apply_frame(grid, palette, 2, palette[0], corner_color=palette[3])
    assert new_palette == palette
    assert framed[0, 0] == framed[1, 1] == framed[-1, -1] == framed[0, -2] == 3  # White
    assert framed[0, 2] == framed[2, 0] == 0  # Rahmen zwischen den Ecken: Black


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
