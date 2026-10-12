"""Gemeinsame Test-Fixtures: Mini-Katalog und Test-Konfiguration.

Der Mini-Katalog ist absichtlich so gebaut, dass jede Palettenregel einmal greift:

=========  ====  =====================================  =============================
Farbe      ID    Verfügbare Teile                       Erwartung
=========  ====  =====================================  =============================
Black      0     Plate 1×1, 1×2, 1×4, 2×4; Tile 1×1     in Palette
Blue       1     Plate 1×1                              in Palette (nur 1×1)
Green      2     Plate 1×2                              raus: kein 1×1-Teil
Red        4     Plate 1×1, 1×2                         in Palette
Yellow     14    Tile 1×1                               nur mit Teilart TILE
White      15    Plate 1×1, 1×2, 2×4; Tile 1×1; Brick   in Palette
Trans-Red  36    Plate 1×1                              raus: transparent
=========  ====  =====================================  =============================
"""

from pathlib import Path

import pytest

from lego_mosaik.config import (
    AppConfig,
    DitheringConfig,
    MosaicConfig,
    PaletteConfig,
    SourceConfig,
    TilingConfig,
)
from lego_mosaik.domain.models import LegoColor, PartCatalog, PartKind, PartType

# (id, name, rgb_hex, is_trans) – wie in Rebrickables colors.csv
COLOR_ROWS: tuple[tuple[int, str, str, bool], ...] = (
    (0, "Black", "05131D", False),
    (1, "Blue", "0055BF", False),
    (2, "Green", "237841", False),
    (4, "Red", "C91A09", False),
    (14, "Yellow", "F2CD37", False),
    (15, "White", "FFFFFF", False),
    (36, "Trans-Red", "C91A09", True),
)

# (part_num, name, kind, width, length, height_ldu)
PART_ROWS: tuple[tuple[str, str, PartKind, int, int, int], ...] = (
    ("3024", "Plate 1 x 1", PartKind.PLATE, 1, 1, 8),
    ("3023", "Plate 1 x 2", PartKind.PLATE, 1, 2, 8),
    ("3710", "Plate 1 x 4", PartKind.PLATE, 1, 4, 8),
    ("3020", "Plate 2 x 4", PartKind.PLATE, 2, 4, 8),
    ("3070b", "Tile 1 x 1 with Groove", PartKind.TILE, 1, 1, 8),
    ("3005", "Brick 1 x 1", PartKind.BRICK, 1, 1, 24),
)

# (part_num, color_id) – welche Kombinationen es gibt
AVAILABLE: tuple[tuple[str, int], ...] = (
    ("3024", 0),
    ("3023", 0),
    ("3710", 0),
    ("3020", 0),
    ("3070b", 0),
    ("3024", 1),
    ("3023", 2),
    ("3024", 4),
    ("3023", 4),
    ("3070b", 14),
    ("3024", 15),
    ("3023", 15),
    ("3020", 15),
    ("3070b", 15),
    ("3005", 15),
    ("3024", 36),
)


def hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
    """Wandelt ``"FF8000"`` in ``(255, 128, 0)`` um (int(..., 16) liest Hexadezimal)."""
    return (int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16))


def make_catalog() -> PartCatalog:
    """Baut den Mini-Katalog direkt aus den Tabellen oben."""
    colors = {
        color_id: LegoColor(color_id, name, hex_to_rgb(rgb), is_trans, ldraw_code=color_id)
        for color_id, name, rgb, is_trans in COLOR_ROWS
    }
    part_types = {
        part_num: PartType(part_num, name, kind, width, length, f"{part_num}.dat", height)
        for part_num, name, kind, width, length, height in PART_ROWS
    }
    element_ids = {combo: f"{index + 300000}" for index, combo in enumerate(AVAILABLE)}
    return PartCatalog(colors, part_types, frozenset(AVAILABLE), element_ids)


@pytest.fixture
def catalog() -> PartCatalog:
    """Mini-Katalog für Domain-, Dithering- und Zerlegungstests."""
    return make_catalog()


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    """Kleine, schnelle Konfiguration mit Ausgabeordner im Temp-Verzeichnis."""
    return AppConfig(
        mosaic=MosaicConfig(width_studs=8, height_studs=8),
        palette=PaletteConfig(part_kinds=(PartKind.PLATE, PartKind.TILE)),
        dithering=DitheringConfig(method="floyd_steinberg"),
        tiling=TilingConfig(strategy="greedy"),
        outputs=("preview_png", "ldraw", "parts_list_csv", "result_json"),
        source=SourceConfig(data_dir=tmp_path / "rebrickable", auto_update=False),
        output_dir=tmp_path / "ausgabe",
    )
