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

Unterbau: Grundplatte 16 × 16 in White, LEGO-Art-Paneel 16 × 16 und Technic-Pin in Black.
"""

import csv
import dataclasses
import gzip
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path

import httpx
import numpy as np
import numpy.typing as npt
import pytest
from PIL import Image

from lego_mosaik.config import (
    AppConfig,
    DitheringConfig,
    LayoutConfig,
    MosaicConfig,
    PaletteConfig,
    SourceConfig,
    TilingConfig,
)
from lego_mosaik.domain.models import ColorCategory, LegoColor, PartCatalog, PartKind, PartType
from lego_mosaik.main import build_catalog_source, build_components, build_updater
from lego_mosaik.pipeline import MosaicPipeline
from lego_mosaik.registry import DITHERERS


def pytest_addoption(parser: pytest.Parser) -> None:
    """Eigene Option: Golden Files neu erzeugen statt vergleichen."""
    parser.addoption(
        "--golden-aktualisieren",
        action="store_true",
        default=False,
        help="Golden Files in tests/golden/ neu schreiben.",
    )


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
    ("3867", "Baseplate 16 x 16", PartKind.BASEPLATE, 16, 16, 4),
    ("65803", "Brick Special 16 x 16 x 1 1/3 with Pin Holes", PartKind.PANEL, 16, 16, 32),
    ("2780", "Technic Pin with Friction Ridges", PartKind.CONNECTOR, 1, 2, 12),
)

# Abweichende BrickLink-Teilenummern (wie in daten/teil_geometrie.csv)
BRICKLINK_IDS = {"3070b": "3070"}

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
    ("3867", 15),
    ("65803", 0),
    ("2780", 0),
)


def hex_to_rgb(hex_value: str) -> tuple[int, int, int]:
    """Wandelt ``"FF8000"`` in ``(255, 128, 0)`` um (int(..., 16) liest Hexadezimal)."""
    return (int(hex_value[0:2], 16), int(hex_value[2:4], 16), int(hex_value[4:6], 16))


def make_catalog() -> PartCatalog:
    """Baut den Mini-Katalog direkt aus den Tabellen oben."""
    colors = {
        color_id: LegoColor(
            color_id,
            name,
            hex_to_rgb(rgb),
            is_trans,
            ldraw_code=color_id,
            category=ColorCategory.TRANSPARENT if is_trans else ColorCategory.SOLID,
        )
        for color_id, name, rgb, is_trans in COLOR_ROWS
    }
    part_types = {
        part_num: PartType(
            part_num,
            name,
            kind,
            width,
            length,
            f"{part_num}.dat",
            height,
            bricklink_id=BRICKLINK_IDS.get(part_num),
        )
        for part_num, name, kind, width, length, height in PART_ROWS
    }
    element_ids = {combo: _element_id(index) for index, combo in enumerate(AVAILABLE)}
    return PartCatalog(colors, part_types, frozenset(AVAILABLE), element_ids)


def _element_id(index: int) -> str:
    """Element-ID der index-ten verfügbaren Kombination (deterministisch)."""
    return str(300000 + index)


def write_csv(
    path: Path, header: Sequence[str], rows: Iterable[Sequence[object]], *, compressed: bool
) -> None:
    """Schreibt eine CSV-Datei, wahlweise gzip-komprimiert wie bei Rebrickable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if compressed else open
    with opener(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_rebrickable_dumps(data_dir: Path) -> None:
    """Schreibt Mini-Versionen von colors/parts/elements.csv.gz.

    Enthält absichtlich Störfälle: Sonderfarben -1/9999, zusätzliche Spalten,
    einen Teilnamen mit Komma, ein Teil außerhalb der Whitelist und doppelte
    Element-IDs (die kleinste muss gewinnen).
    """
    color_rows = [(-1, "[Unknown]", "0033B2", "False", 1), (9999, "[No Color]", "05131D", "f", 1)]
    color_rows += [(cid, name, rgb, str(trans), 7) for cid, name, rgb, trans in COLOR_ROWS]
    write_csv(
        data_dir / "colors.csv.gz",
        ("id", "name", "rgb", "is_trans", "num_parts"),
        color_rows,
        compressed=True,
    )
    part_rows = [(num, name, 14, "Plastic") for num, name, *_ in PART_ROWS]
    part_rows.append(("9999x", "Brick, Modified 1 x 2", 11, "Plastic"))
    write_csv(
        data_dir / "parts.csv.gz",
        ("part_num", "name", "part_cat_id", "part_material"),
        part_rows,
        compressed=True,
    )
    element_rows = [(_element_id(i), num, cid, "") for i, (num, cid) in enumerate(AVAILABLE)]
    element_rows += [("9900001", "3024", 0, ""), ("4211", "9999x", 0, "")]
    write_csv(
        data_dir / "elements.csv.gz",
        ("element_id", "part_num", "color_id", "design_id"),
        element_rows,
        compressed=True,
    )


def write_geometry(path: Path) -> None:
    """Schreibt eine Mini-``teil_geometrie.csv`` passend zu ``PART_ROWS``."""
    header = (
        "part_num",
        "kind",
        "width_studs",
        "length_studs",
        "ldraw_file",
        "height_ldu",
        "bricklink_id",
    )
    rows = [
        (num, kind.value, w, length, f"{num}.dat", h, BRICKLINK_IDS.get(num, ""))
        for num, _, kind, w, length, h in PART_ROWS
    ]
    write_csv(path, header, rows, compressed=False)


@pytest.fixture
def source_config(tmp_path: Path) -> SourceConfig:
    """Quelle mit Mini-Dumps und Mini-Geometrie im Temp-Verzeichnis."""
    data_dir = tmp_path / "rebrickable"
    write_rebrickable_dumps(data_dir)
    write_geometry(tmp_path / "teil_geometrie.csv")
    return SourceConfig(
        data_dir=data_dir,
        geometry_file=tmp_path / "teil_geometrie.csv",
        color_mapping_file=tmp_path / "farb_zuordnung.csv",
        studio_color_file=None,  # Tests dürfen nicht von einer lokalen Studio-Installation abhängen
        auto_update=False,
    )


@pytest.fixture
def catalog() -> PartCatalog:
    """Mini-Katalog für Domain-, Dithering- und Zerlegungstests."""
    return make_catalog()


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    """Kleine, schnelle Konfiguration mit Ausgabeordner im Temp-Verzeichnis."""
    return AppConfig(
        layout=LayoutConfig(plate_studs=16),  # Grundplatte 16 × 16, Rahmen 1 Noppe
        mosaic=MosaicConfig(),
        palette=PaletteConfig(part_kinds=(PartKind.PLATE, PartKind.TILE)),
        dithering=DitheringConfig(method="floyd_steinberg"),
        tiling=TilingConfig(strategy="greedy"),
        outputs=("preview_png", "ldraw", "parts_list_csv", "result_json"),
        source=SourceConfig(
            data_dir=tmp_path / "rebrickable", studio_color_file=None, auto_update=False
        ),
        output_dir=tmp_path / "ausgabe",
    )


# --- Testbilder (8 × 8), direkt als Arrays erzeugt ----------------------------------

type RgbArray = npt.NDArray[np.uint8]


def solid_image(rgb: tuple[int, int, int], size: int = 8) -> RgbArray:
    """Einfarbiges RGB-Bild (size × size)."""
    # np.full füllt ein Array der Form (H, W, 3) mit dem RGB-Tupel (per Broadcasting)
    return np.full((size, size, 3), rgb, dtype=np.uint8)


def gray_gradient(size: int = 8) -> RgbArray:
    """Horizontaler Grauverlauf von Schwarz nach Weiß."""
    values = np.linspace(0, 255, size).round().astype(np.uint8)
    # np.tile(values, (size, 1)) wiederholt die Zeile size-mal → (H, W); dann auf 3 Kanäle
    return np.repeat(np.tile(values, (size, 1))[:, :, np.newaxis], 3, axis=2)


def color_blocks(size: int = 8) -> RgbArray:
    """Vier Quadranten in Rot, Blau, Weiß und Schwarz."""
    image = np.zeros((size, size, 3), dtype=np.uint8)
    half = size // 2
    image[:half, :half] = (201, 26, 9)  # Red
    image[:half, half:] = (0, 85, 191)  # Blue
    image[half:, :half] = (255, 255, 255)  # White
    return image


# --- Pipeline-Fixtures -------------------------------------------------------------

FIXED_NOW = datetime(2026, 10, 12, 14, 30, 5)


def _offline_transport() -> httpx.MockTransport:
    """Transport, der jede Anfrage ablehnt: Tests dürfen nie ins Netz."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Tests laufen offline", request=request)

    return httpx.MockTransport(refuse)


@pytest.fixture
def pipeline_config(app_config: AppConfig, source_config: SourceConfig) -> AppConfig:
    """Konfiguration mit Mini-Katalog, 16er-Grundplatte (White) und allen Ausgaben."""
    return dataclasses.replace(
        app_config,
        source=source_config,
        layout=dataclasses.replace(app_config.layout, substrate_color_id=15),
        outputs=("preview_png", "ldraw", "parts_list_csv", "official_parts_lists", "result_json"),
    )


def make_pipeline(config: AppConfig) -> MosaicPipeline:
    """Pipeline wie in main.build_pipeline, aber offline und mit fester Uhr."""
    return MosaicPipeline(
        config,
        catalog_source=build_catalog_source(config.source),
        updater=build_updater(config.source, transport=_offline_transport()),
        component_factory=build_components,
        method_names={"Dithering": tuple(DITHERERS)},
        now=lambda: FIXED_NOW,
    )


@pytest.fixture
def image_file(tmp_path: Path) -> Path:
    """PNG mit vier Farbquadranten (32 × 32 Pixel)."""
    path = tmp_path / "bild.png"
    Image.fromarray(color_blocks(32)).save(path)
    return path
