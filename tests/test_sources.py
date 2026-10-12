"""Tests für das Einlesen der Rebrickable-CSV-Dumps."""

import dataclasses
from pathlib import Path

import pytest
from conftest import make_catalog, write_csv

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.main import build_catalog_source
from lego_mosaik.sources.rebrickable_csv import parse_hex_rgb


def test_loads_expected_catalog(source_config: SourceConfig) -> None:
    loaded = build_catalog_source(source_config).load_catalog()
    expected = make_catalog()
    assert dict(loaded.colors) == dict(expected.colors)
    assert dict(loaded.part_types) == dict(expected.part_types)
    assert loaded.available == expected.available
    # Die doppelte, größere Element-ID 9900001 für (3024, Black) darf nicht gewinnen
    assert dict(loaded.element_ids) == dict(expected.element_ids)


def test_special_colors_and_unlisted_parts_are_excluded(source_config: SourceConfig) -> None:
    catalog = build_catalog_source(source_config).load_catalog()
    assert -1 not in catalog.colors
    assert 9999 not in catalog.colors
    assert "9999x" not in catalog.part_types


def test_color_mapping_overrides_ldraw_code(source_config: SourceConfig) -> None:
    write_csv(
        source_config.color_mapping_file,
        ("rebrickable_id", "ldraw_code"),
        [(36, 36000)],
        compressed=False,
    )
    catalog = build_catalog_source(source_config).load_catalog()
    assert catalog.colors[36].ldraw_code == 36000
    assert catalog.colors[0].ldraw_code == 0


def test_catalog_is_cached_until_invalidated(source_config: SourceConfig) -> None:
    source = build_catalog_source(source_config)
    first = source.load_catalog()
    assert source.load_catalog() is first
    source.invalidate()
    assert source.load_catalog() is not first


def test_missing_file_mentions_update_command(tmp_path: Path) -> None:
    config = SourceConfig(data_dir=tmp_path / "leer", geometry_file=tmp_path / "fehlt.csv")
    with pytest.raises(CatalogError, match="teile-aktualisieren"):
        build_catalog_source(config).load_catalog()


def test_missing_column_is_reported(source_config: SourceConfig) -> None:
    write_csv(
        source_config.data_dir / "colors.csv.gz", ("id", "name"), [(0, "Black")], compressed=True
    )
    with pytest.raises(CatalogError, match="fehlen die Spalten: rgb, is_trans"):
        build_catalog_source(source_config).load_catalog()


def test_invalid_row_reports_line_number(source_config: SourceConfig, tmp_path: Path) -> None:
    geometry = tmp_path / "kaputt.csv"
    header = ("part_num", "kind", "width_studs", "length_studs", "ldraw_file", "height_ldu")
    write_csv(
        geometry,
        header,
        [("3024", "plate", 1, 1, "3024.dat", 8), ("3023", "platte", 1, 2, "3023.dat", 8)],
        compressed=False,
    )
    config = dataclasses.replace(source_config, geometry_file=geometry)
    with pytest.raises(CatalogError, match=r"kaputt\.csv, Zeile 3"):
        build_catalog_source(config).load_catalog()


def test_parse_hex_rgb() -> None:
    assert parse_hex_rgb("05131D") == (5, 19, 29)
    assert parse_hex_rgb("#FFFFFF") == (255, 255, 255)
    with pytest.raises(ValueError, match="ungültiger Farbwert"):
        parse_hex_rgb("FFF")
