"""Tests für die Ausgabe-Writer."""

import csv
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.layout import (
    connector_placements,
    find_connector,
    find_substrate_part,
    grid_layout_size,
    substrate_placements,
)
from lego_mosaik.domain.models import LongAxis, Mosaic, PartCatalog, PartKind, Placement
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.outputs.ldraw import ldraw_lines, part_y, placement_line
from lego_mosaik.registry import OUTPUT_WRITERS
from lego_mosaik.tiling.greedy import GreedyTiling


@pytest.fixture
def mosaic(catalog: PartCatalog) -> Mosaic:
    """8 × 8-Mosaik: oben Black, unten links Red, unten rechts White (Greedy-Zerlegung)."""
    palette = build_palette(catalog, (PartKind.PLATE,))  # Black, Blue, Red, White
    grid = np.zeros((8, 8), dtype=np.intp)
    grid[4:, :4] = 2
    grid[4:, 4:] = 3
    placements = GreedyTiling((PartKind.PLATE,), 8).tile(grid, palette, catalog)
    return Mosaic(
        "test",
        8,
        8,
        grid,
        palette,
        tuple(placements),
        element_ids=catalog.element_ids,
        metadata={"statistik": {"teile": len(placements)}},
    )


def _line(placement: Placement) -> str:
    """LDraw-Zeile eines gestapelten Teils (Höhe aus ``part_y``)."""
    return placement_line(placement, y=part_y(placement))


def test_placement_line_unrotated(catalog: PartCatalog) -> None:
    placement = Placement(catalog.part_types["3020"], catalog.colors[0], row=0, col=0)
    # 2×4 ungedreht: 4 Spalten breit, 2 Zeilen hoch → Mitte bei x = 2 · 20, z = 1 · 20
    assert _line(placement) == "1 0 40 0 20 1 0 0 0 1 0 0 0 1 3020.dat"


def test_placement_line_rotated(catalog: PartCatalog) -> None:
    placement = Placement(catalog.part_types["3020"], catalog.colors[15], 2, 5, rotated=True)
    # gedreht: 2 Spalten, 4 Zeilen → x = (5 + 1) · 20, z = (2 + 2) · 20
    assert _line(placement) == "1 15 120 0 80 0 0 1 0 1 0 -1 0 0 3020.dat"


def test_long_axis_z_inverts_rotation(catalog: PartCatalog) -> None:
    part = dataclasses.replace(catalog.part_types["3710"], ldraw_long_axis=LongAxis.Z)
    flat = Placement(part, catalog.colors[0], 0, 0, rotated=False)
    turned = Placement(part, catalog.colors[0], 0, 0, rotated=True)
    assert " 0 0 1 0 1 0 -1 0 0 " in _line(flat)
    assert " 1 0 0 0 1 0 0 0 1 " in _line(turned)


def test_heights_of_stacked_parts(catalog: PartCatalog) -> None:
    tile, brick = catalog.part_types["3070b"], catalog.part_types["3005"]
    # Ursprung = Oberkante; y zeigt nach unten: höher liegende Teile haben kleineres y
    assert part_y(Placement(tile, catalog.colors[0], 0, 0)) == 0  # direkt auf dem Unterbau
    assert part_y(Placement(tile, catalog.colors[0], 0, 0, bottom_ldu=8)) == -8  # erhöht
    assert part_y(Placement(brick, catalog.colors[15], 0, 0)) == -16  # Brick: 24 LDU hoch


def test_ldraw_header_and_steps(mosaic: Mosaic) -> None:
    lines = ldraw_lines(mosaic, rows_per_step=4, section_studs=16)
    assert lines[:4] == [
        "0 test",
        "0 Name: mosaik.ldr",
        "0 Author: lego-mosaik",
        "0 !LDRAW_ORG Unofficial_Model",
    ]
    assert lines.count("0 STEP") == 2  # 8 Zeilen / 4 Zeilen pro Schritt
    assert sum(line.startswith("1 ") for line in lines) == len(mosaic.placements)


def test_ldraw_steps_sorted_by_color(mosaic: Mosaic) -> None:
    lines = ldraw_lines(mosaic, rows_per_step=8, section_studs=16)
    colors = [int(line.split()[1]) for line in lines if line.startswith("1 ")]
    assert colors == sorted(colors)  # ein Schritt → komplett nach Farbe sortiert


def test_baseplates_are_first_step(mosaic: Mosaic, catalog: PartCatalog) -> None:
    baseplate = dataclasses.replace(
        catalog.part_types["3020"],
        part_num="3811",
        kind=PartKind.BASEPLATE,
        width_studs=32,
        length_studs=32,
        ldraw_file="3811.dat",
    )
    plates = substrate_placements(baseplate, catalog.colors[15], plates_wide=2, plates_high=1)
    lines = ldraw_lines(dataclasses.replace(mosaic, substrate=plates), 8, 16)
    assert lines[4:8] == [
        "0 // Grundplatten",
        "1 15 320 8 320 1 0 0 0 1 0 0 0 1 3811.dat",  # Mitte der ersten Platte: 16 · 20 LDU
        "1 15 960 8 320 1 0 0 0 1 0 0 0 1 3811.dat",  # zweite Platte 32 Noppen weiter rechts
        "0 STEP",
    ]


def test_art_panels_pins_and_frame_order(mosaic: Mosaic, catalog: PartCatalog) -> None:
    panel, pin = find_substrate_part(catalog, PartKind.PANEL, 16), find_connector(catalog)
    size = grid_layout_size(16, plates_wide=2, plates_high=1, frame_width=0)
    panels = substrate_placements(panel, catalog.colors[0], 2, 1)
    pins = connector_placements(pin, catalog.colors[0], size, 16, pins_per_seam=2)
    frame = (Placement(catalog.part_types["3070b"], catalog.colors[0], 0, 0, bottom_ldu=8),)
    support = (Placement(catalog.part_types["3024"], catalog.colors[0], 0, 0),)
    full = dataclasses.replace(
        mosaic, substrate=panels, connectors=pins, frame=frame, supports=support
    )
    lines = ldraw_lines(full, rows_per_step=8, section_studs=16, connector_depth_ldu=16)
    comments = [line for line in lines if line.startswith("0 //")]
    assert comments == [
        "0 // LEGO-Art-Paneele",
        "0 // Verbinder (Technic-Pins)",
        "0 // Rahmen-Unterbau, Lage 1",
        "0 // Rahmen",
        "0 // Abschnitt 1",
    ]
    pin_lines = [line for line in lines if line.endswith("2780.dat")]
    # Pin an der Naht x = 16 · 20, Zeile 4 → z = 4,5 · 20; Lochmitte 16 LDU unter Oberkante 8
    assert pin_lines[0] == "1 0 320 24 90 1 0 0 0 1 0 0 0 1 2780.dat"
    assert any(line.startswith("1 0 10 -8 10 ") for line in lines)  # Tile auf der Stütz-Plate


def test_all_writers_create_files(mosaic: Mosaic, app_config: AppConfig, tmp_path: Path) -> None:
    written = {
        name: OUTPUT_WRITERS[name](app_config).write(mosaic, tmp_path)
        for name in app_config.outputs
    }
    assert {path.name for path in written.values()} == {
        "vorschau.png",
        "mosaik.ldr",
        "teileliste.csv",
        "ergebnis.json",
    }
    assert all(path.is_file() for path in written.values())


def test_preview_has_expected_size(mosaic: Mosaic, app_config: AppConfig, tmp_path: Path) -> None:
    path = OUTPUT_WRITERS["preview_png"](app_config).write(mosaic, tmp_path)
    with Image.open(path) as image:
        assert image.size == (8 * app_config.preview.stud_px, 8 * app_config.preview.stud_px)


def test_parts_lists(mosaic: Mosaic, app_config: AppConfig, tmp_path: Path) -> None:
    path = OUTPUT_WRITERS["parts_list_csv"](app_config).write(mosaic, tmp_path)
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")  # UTF-8-BOM für Excel
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert sum(int(row["Anzahl"]) for row in rows) == len(mosaic.placements)
    assert all(row["Element-ID"] for row in rows)
    assert [int(row["Farb-ID"]) for row in rows] == sorted(int(row["Farb-ID"]) for row in rows)
    rebrickable = (tmp_path / "teileliste_rebrickable.csv").read_text(encoding="utf-8-sig")
    assert rebrickable.splitlines()[0] == "Part,Color,Quantity"


def test_result_json(mosaic: Mosaic, app_config: AppConfig, tmp_path: Path) -> None:
    OUTPUT_WRITERS["ldraw"](app_config).write(mosaic, tmp_path)
    path = OUTPUT_WRITERS["result_json"](app_config).write(mosaic, tmp_path)
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["dateien"] == ["ergebnis.json", "mosaik.ldr"]
    assert result["statistik"] == {"teile": len(mosaic.placements)}
    assert result["anzahl_teile"] == len(mosaic.placements)
    assert sum(color["noppen"] for color in result["palette"]) == 64
    assert [color["name"] for color in result["palette"]] == ["Black", "Red", "White"]


def test_steps_follow_sections(catalog: PartCatalog) -> None:
    palette = build_palette(catalog, (PartKind.PLATE,))
    grid = np.zeros((4, 32), dtype=np.intp)  # zwei Abschnitte à 16 nebeneinander
    placements = tuple(GreedyTiling((PartKind.PLATE,), 8).tile(grid, palette, catalog))
    mosaic = Mosaic("test", 32, 4, grid, palette, placements)
    lines = ldraw_lines(mosaic, rows_per_step=2, section_studs=16)
    comments = [line for line in lines if line.startswith("0 //")]
    assert comments == ["0 // Abschnitt 1", "0 // Abschnitt 2"]
    assert lines.count("0 STEP") == 4  # je Abschnitt 2 Zeilenblöcke à 2 Zeilen
