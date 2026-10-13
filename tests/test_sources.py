"""Tests für das Einlesen der Rebrickable-CSV-Dumps."""

import dataclasses
from datetime import date
from pathlib import Path

import pytest
from conftest import COLOR_ROWS, make_catalog, write_csv

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.main import build_catalog_source
from lego_mosaik.sources.rebrickable_csv import RebrickableCsvSource, parse_hex_rgb


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


def _recent_source(config: SourceConfig) -> RebrickableCsvSource:
    """Quelle mit „nur aktuelle Teile“ (3 Jahre) und fester Uhr im Jahr 2026."""
    return RebrickableCsvSource(
        data_dir=config.data_dir,
        geometry_file=config.geometry_file,
        color_mapping_file=config.color_mapping_file,
        studio_color_file=None,
        category_keywords=config.category_keywords,
        recent_years=3,
        today=lambda: date(2026, 6, 1),
    )


def _write_color_years(config: SourceConfig, last_years: dict[int, int]) -> None:
    """colors.csv mit Spalte y2 (letztes Jahr der Farbe); fehlende Farben: 2026."""
    rows = [
        (cid, name, rgb, str(trans), 1990, last_years.get(cid, 2026))
        for cid, name, rgb, trans in COLOR_ROWS
    ]
    header = ("id", "name", "rgb", "is_trans", "y1", "y2")
    write_csv(config.data_dir / "colors.csv.gz", header, rows, compressed=True)


def test_retired_colors_are_dropped_without_inventories(source_config: SourceConfig) -> None:
    _write_color_years(source_config, {2: 2005})  # Green gibt es seit 2005 nicht mehr
    catalog = _recent_source(source_config).load_catalog()
    assert catalog.colors[2].last_year == 2005
    assert ("3023", 2) not in catalog.available
    assert ("3023", 15) in catalog.available


def test_only_combinations_from_recent_sets(source_config: SourceConfig) -> None:
    data_dir = source_config.data_dir
    sets = [("alt-1", 2010), ("neu-1", 2025)]
    write_csv(
        data_dir / "sets.csv.gz",
        ("set_num", "name", "year"),
        [(n, n, y) for n, y in sets],
        compressed=True,
    )
    write_csv(
        data_dir / "inventories.csv.gz",
        ("id", "version", "set_num"),
        [(1, 1, "alt-1"), (2, 1, "neu-1")],
        compressed=True,
    )
    inventory = [(1, "3024", 4), (2, "3024", 15), (2, "3023", 15), (1, "3023", 4)]
    write_csv(
        data_dir / "inventory_parts.csv.gz",
        ("inventory_id", "part_num", "color_id", "quantity", "is_spare"),
        [(*row, 1, "False") for row in inventory],
        compressed=True,
    )
    catalog = _recent_source(source_config).load_catalog()
    # Plate 1 × 1 in Rot nur aus einem Set von 2010 → ausgelaufen; in Weiß aktuell
    assert ("3024", 4) not in catalog.available
    assert ("3024", 15) in catalog.available
    assert ("3024", 4) not in catalog.element_ids
    # Teile, die in keinem aktuellen Set vorkommen (z. B. Paneele), behalten ihre Farben
    assert ("65803", 0) in catalog.available


def test_parse_hex_rgb() -> None:
    assert parse_hex_rgb("05131D") == (5, 19, 29)
    assert parse_hex_rgb("#FFFFFF") == (255, 255, 255)
    with pytest.raises(ValueError, match="ungültiger Farbwert"):
        parse_hex_rgb("FFF")
