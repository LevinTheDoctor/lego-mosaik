"""Tests für die Pipeline (offline, mit Mini-Katalog)."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
from conftest import make_pipeline

from lego_mosaik.config import AppConfig, MosaicOverrides
from lego_mosaik.domain.errors import ConfigError, DownloadError
from lego_mosaik.domain.models import ColorCategory
from lego_mosaik.pipeline import safe_name


def test_run_creates_all_outputs(pipeline_config: AppConfig, image_file: Path) -> None:
    result = make_pipeline(pipeline_config).run(image_file, title="Katze & Hund")
    assert result.id == "2026-10-12_143005_Katze_Hund"
    assert set(result.files) == set(pipeline_config.outputs)
    names = {path.name for path in result.target_dir.iterdir()}
    result_json = json.loads((result.target_dir / "ergebnis.json").read_text(encoding="utf-8"))
    assert set(result_json["dateien"]) == names  # result_json lief zuletzt
    assert result_json["konfiguration"]["layout"]["baseplate_studs"] == 16


def test_mosaic_has_frame_baseplate_and_full_coverage(
    pipeline_config: AppConfig, image_file: Path
) -> None:
    mosaic = make_pipeline(pipeline_config).run(image_file).mosaic
    assert (mosaic.width, mosaic.height) == (16, 16)
    assert [plate.part.part_num for plate in mosaic.baseplates] == ["3867"]
    border = {mosaic.color_at(0, col).name for col in range(16)}
    border |= {mosaic.color_at(row, 0).name for row in range(16)}
    assert border == {"Black"}
    assert sum(p.part.area for p in mosaic.placements) == 16 * 16


def test_without_baseplate_and_frame(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(include_baseplate=False, frame_width=0)
    result = make_pipeline(pipeline_config).run(image_file, overrides)
    assert result.mosaic.baseplates == ()
    assert result.stats["bild_breite"] == 16


def test_runs_are_deterministic(pipeline_config: AppConfig, image_file: Path) -> None:
    pipeline = make_pipeline(pipeline_config)
    first, second = pipeline.run(image_file), pipeline.run(image_file)
    assert first.target_dir != second.target_dir  # Zähler-Suffix bei gleichem Zeitstempel
    assert np.array_equal(first.mosaic.color_grid, second.mosaic.color_grid)
    ldr = [(r.target_dir / "mosaik.ldr").read_bytes() for r in (first, second)]
    assert ldr[0] == ldr[1]


def test_max_colors_limits_palette(pipeline_config: AppConfig, image_file: Path) -> None:
    config = dataclasses.replace(
        pipeline_config, palette=dataclasses.replace(pipeline_config.palette, max_colors=2)
    )
    mosaic = make_pipeline(config).run(image_file).mosaic
    inner = {mosaic.color_at(r, c).id for r in range(1, 15) for c in range(1, 15)}
    assert len(inner) <= 2


def test_empty_palette_is_config_error(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(color_categories=(ColorCategory.CHROME,))
    with pytest.raises(ConfigError, match="keine Farbe übrig"):
        make_pipeline(pipeline_config).run(image_file, overrides)


def test_unavailable_baseplate_color(pipeline_config: AppConfig, image_file: Path) -> None:
    config = dataclasses.replace(
        pipeline_config, layout=dataclasses.replace(pipeline_config.layout, baseplate_color_id=0)
    )
    with pytest.raises(ConfigError, match="Grundplatte 3867 gibt es nicht in Farbe 0"):
        make_pipeline(config).run(image_file)


def test_auto_update_without_network_and_files_fails(
    pipeline_config: AppConfig, image_file: Path, tmp_path: Path
) -> None:
    source = dataclasses.replace(
        pipeline_config.source, data_dir=tmp_path / "leer", auto_update=True
    )
    config = dataclasses.replace(pipeline_config, source=source)
    with pytest.raises(DownloadError, match="Teilekatalog nicht verfügbar"):
        make_pipeline(config).run(image_file)


def test_safe_name() -> None:
    assert safe_name("Katze & Hund") == "Katze_Hund"
    assert safe_name("../../etc") == "etc"
    assert safe_name("äöü") == "mosaik"


def test_no_part_crosses_a_section(pipeline_config: AppConfig, image_file: Path) -> None:
    config = dataclasses.replace(
        pipeline_config,
        instructions=dataclasses.replace(pipeline_config.instructions, section_studs=8),
    )
    mosaic = make_pipeline(config).run(image_file).mosaic
    for placement in mosaic.placements:
        cells = list(placement.cells())
        assert len({(row // 8, col // 8) for row, col in cells}) == 1
