"""Tests für die Ausgabe-Writer."""

import csv
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.models import LongAxis, Mosaic, PartCatalog, PartKind, Placement
from lego_mosaik.domain.palette import build_palette
from lego_mosaik.outputs.ldraw import baseplate_lines, ldraw_lines, placement_line
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
        catalog.element_ids,
        {"statistik": {"teile": len(placements)}},
    )


def test_placement_line_unrotated(catalog: PartCatalog) -> None:
    placement = Placement(catalog.part_types["3020"], catalog.colors[0], row=0, col=0)
    # 2×4 ungedreht: 4 Spalten breit, 2 Zeilen hoch → Mitte bei x = 2 · 20, z = 1 · 20
    assert placement_line(placement) == "1 0 40 0 20 1 0 0 0 1 0 0 0 1 3020.dat"


def test_placement_line_rotated(catalog: PartCatalog) -> None:
    placement = Placement(catalog.part_types["3020"], catalog.colors[15], 2, 5, rotated=True)
    # gedreht: 2 Spalten, 4 Zeilen → x = (5 + 1) · 20, z = (2 + 2) · 20
    assert placement_line(placement) == "1 15 120 0 80 0 0 1 0 1 0 -1 0 0 3020.dat"


def test_long_axis_z_inverts_rotation(catalog: PartCatalog) -> None:
    part = dataclasses.replace(catalog.part_types["3710"], ldraw_long_axis=LongAxis.Z)
    flat = Placement(part, catalog.colors[0], 0, 0, rotated=False)
    turned = Placement(part, catalog.colors[0], 0, 0, rotated=True)
    assert " 0 0 1 0 1 0 -1 0 0 " in placement_line(flat)
    assert " 1 0 0 0 1 0 0 0 1 " in placement_line(turned)


def test_ldraw_header_and_steps(mosaic: Mosaic) -> None:
    lines = ldraw_lines(mosaic, rows_per_step=4, include_baseplate=False)
    assert lines[:4] == [
        "0 test",
        "0 Name: mosaik.ldr",
        "0 Author: lego-mosaik",
        "0 !LDRAW_ORG Unofficial_Model",
    ]
    assert lines.count("0 STEP") == 2  # 8 Zeilen / 4 Zeilen pro Schritt
    assert sum(line.startswith("1 ") for line in lines) == len(mosaic.placements)


def test_ldraw_steps_sorted_by_color(mosaic: Mosaic) -> None:
    lines = ldraw_lines(mosaic, rows_per_step=8, include_baseplate=False)
    colors = [int(line.split()[1]) for line in lines if line.startswith("1 ")]
    assert colors == sorted(colors)  # ein Schritt → komplett nach Farbe sortiert


def test_baseplates_cover_mosaic() -> None:
    lines = baseplate_lines(48, 40)
    assert len(lines) == 4  # aufgerundet 2 × 2 Grundplatten
    assert lines[0] == "1 71 320 8 320 1 0 0 0 1 0 0 0 1 3811.dat"


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
        rows = list(csv.DictReader(handle))
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
