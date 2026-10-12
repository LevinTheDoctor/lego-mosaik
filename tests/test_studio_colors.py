"""Tests für Studios Farbtabelle und die Farbkategorien."""

import dataclasses
from pathlib import Path

import pytest

from lego_mosaik.config import DEFAULT_CATEGORY_KEYWORDS, SourceConfig, parse_color_categories
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import ColorCategory
from lego_mosaik.main import build_catalog_source
from lego_mosaik.sources.studio_colors import (
    category_from_keywords,
    normalize_color_name,
    read_studio_colors,
)

HEADER = (
    "Studio Color Code\tBL Color Code\tLDraw Color Code\tLDD color code\tStudio Color Name\t"
    "BL Color Name\tLDraw Color Name\tLDD Color Name\tRGB value\tAlpha\tCategoryName\n"
)
# Wie in Studio: Black doppelt (zweite Zeile ohne BL-Code), Trans-Red mit eigener Kategorie,
# Red absichtlich mit abweichendem LDraw-Code, um den Namensabgleich zu prüfen
ROWS = (
    "11\t11\t0\t26\tBlack\tBlack\tBlack\tBlack\t#04121C\t1\tSolid Colors\n",
    "11\t\t0\t1012\tBlack\t\tBlack\tCONDUCT.Black\t#04121C\t1\tSolid Colors\n",
    "7\t7\t1\t23\tBlue\tBlue\tBlue\tBright Blue\t#0054BE\t1\tSolid Colors\n",
    "5\t5\t104\t21\tRed\tRed\tRed\tBright red\t#C91A09\t1\tSolid Colors\n",
    "17\t17\t36\t41\tTrans-Red\tTrans-Red\tTrans_Red\tTr. Red\t#C91A09\t0.5\tTransparent Colors\n",
    "1\t1\t15\t1\tWhite\tWhite\tWhite\tWhite\t#FFFFFF\t1\tSolid Colors\n",
)


@pytest.fixture
def studio_file(tmp_path: Path) -> Path:
    path = tmp_path / "StudioColorDefinition.txt"
    path.write_text(HEADER + "".join(ROWS), encoding="utf-8")
    return path


def test_name_matching_finds_ldraw_and_bricklink_codes(
    source_config: SourceConfig, studio_file: Path
) -> None:
    config = dataclasses.replace(source_config, studio_color_file=studio_file)
    colors = build_catalog_source(config).load_catalog().colors
    assert (colors[0].bricklink_code, colors[0].lego_name) == (11, "Black")
    assert colors[4].ldraw_code == 104  # über den Namen "Red", nicht über die ID 4
    assert colors[1].lego_name == "Bright Blue"
    assert colors[36].category is ColorCategory.TRANSPARENT


def test_unmatched_colors_get_no_ldraw_code(source_config: SourceConfig, studio_file: Path) -> None:
    config = dataclasses.replace(source_config, studio_color_file=studio_file)
    colors = build_catalog_source(config).load_catalog().colors
    assert colors[14].ldraw_code is None  # "Yellow" steht nicht in der Mini-Tabelle


def test_override_wins_and_looks_up_by_code(source_config: SourceConfig, studio_file: Path) -> None:
    source_config.color_mapping_file.write_text("rebrickable_id,ldraw_code\n14,15\n")
    config = dataclasses.replace(source_config, studio_color_file=studio_file)
    yellow = build_catalog_source(config).load_catalog().colors[14]
    assert (yellow.ldraw_code, yellow.bricklink_code) == (15, 1)


def test_missing_studio_file_returns_none(tmp_path: Path) -> None:
    assert read_studio_colors(tmp_path / "fehlt.txt") is None
    assert read_studio_colors(None) is None


@pytest.mark.parametrize(
    ("name", "is_trans", "expected"),
    [
        ("Glitter Trans-Clear", True, ColorCategory.GLITTER),
        ("Trans-Red", True, ColorCategory.TRANSPARENT),
        ("Metallic Silver", False, ColorCategory.METALLIC),
        ("Pearl Gold", False, ColorCategory.PEARL),
        ("Flat Silver", False, ColorCategory.PEARL),
        ("Chrome Gold", False, ColorCategory.CHROME),
        ("Reddish Brown", False, ColorCategory.SOLID),
    ],
)
def test_keyword_categories(name: str, is_trans: bool, expected: ColorCategory) -> None:
    assert category_from_keywords(name, is_trans, DEFAULT_CATEGORY_KEYWORDS) is expected


def test_normalize_color_name() -> None:
    assert normalize_color_name("Light_Bluish_Grey") == normalize_color_name("Light Bluish Gray")
    assert normalize_color_name("Trans-Red") == "trans red"


def test_parse_color_categories() -> None:
    assert parse_color_categories("solid, pearl") == (ColorCategory.SOLID, ColorCategory.PEARL)
    with pytest.raises(ConfigError, match="Unbekannte Farbkategorie „gold“"):
        parse_color_categories("gold")
