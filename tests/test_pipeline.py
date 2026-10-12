"""Tests für die Pipeline (offline, mit Mini-Katalog)."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from conftest import make_pipeline
from PIL import Image, ImageDraw

from lego_mosaik.config import AppConfig, MosaicOverrides
from lego_mosaik.domain.errors import ConfigError, DownloadError
from lego_mosaik.domain.layout import frame_mask
from lego_mosaik.domain.models import ColorCategory, Mosaic, PartKind, ReliefMode, Substrate
from lego_mosaik.domain.sections import StepKind, assembly_steps
from lego_mosaik.pipeline import safe_name


def test_run_creates_all_outputs(pipeline_config: AppConfig, image_file: Path) -> None:
    result = make_pipeline(pipeline_config).run(image_file, title="Katze & Hund")
    assert result.id == "2026-10-12_143005_Katze_Hund"
    assert set(result.files) == set(pipeline_config.outputs)
    names = {path.name for path in result.target_dir.iterdir()}
    result_json = json.loads((result.target_dir / "ergebnis.json").read_text(encoding="utf-8"))
    assert set(result_json["dateien"]) == names  # result_json lief zuletzt
    assert result_json["konfiguration"]["layout"]["plate_studs"] == 16


def test_mosaic_has_frame_baseplate_and_full_coverage(
    pipeline_config: AppConfig, image_file: Path
) -> None:
    mosaic = make_pipeline(pipeline_config).run(image_file).mosaic
    assert (mosaic.width, mosaic.height) == (16, 16)
    assert [plate.part.part_num for plate in mosaic.substrate] == ["3867"]
    border = {mosaic.color_at(0, col).name for col in range(16)}
    border |= {mosaic.color_at(row, 0).name for row in range(16)}
    assert border == {"Black"}
    assert sum(p.part.area for p in mosaic.surface) == 16 * 16
    assert sum(p.part.area for p in mosaic.frame) == 16 * 16 - 14 * 14  # Rahmen extra zerlegt


def test_without_substrate_and_frame(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(substrate=Substrate.NONE, frame_width=0)
    result = make_pipeline(pipeline_config).run(image_file, overrides)
    assert result.mosaic.substrate == () and result.mosaic.frame == ()
    assert result.stats["bild_breite"] == 16


def test_art_panels_are_connected_with_pins(pipeline_config: AppConfig, image_file: Path) -> None:
    # Die Test-Konfiguration legt White für die Grundplatte fest; Art-Paneele gibt es nur in Black
    overrides = MosaicOverrides(
        substrate=Substrate.ART_PANEL, plates_wide=2, plates_high=2, substrate_color_id=0
    )
    mosaic = make_pipeline(pipeline_config).run(image_file, overrides).mosaic
    assert (mosaic.width, mosaic.height) == (32, 32)
    assert [p.part.part_num for p in mosaic.substrate] == ["65803"] * 4
    assert {p.color.name for p in mosaic.substrate} == {"Black"}
    assert len(mosaic.connectors) == 4 * 2  # 4 Nähte × 2 Pins


def test_raised_tile_frame_with_supports(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(
        substrate=Substrate.ART_PANEL,
        substrate_color_id=0,
        plates_wide=2,
        plates_high=1,
        frame_part_kinds=(PartKind.TILE,),
        frame_raise_plates=1,
    )
    mosaic = make_pipeline(pipeline_config).run(image_file, overrides).mosaic
    assert {p.part.kind for p in mosaic.frame} == {PartKind.TILE}
    assert {p.bottom_ldu for p in mosaic.frame} == {8}  # eine Plate über dem Bild
    assert {p.part.kind for p in mosaic.supports} == {PartKind.PLATE}
    coverage = np.zeros((mosaic.height, mosaic.width), dtype=int)
    for support in mosaic.supports:
        for row, col in support.cells():
            coverage[row, col] += 1
    assert (coverage == frame_mask(mosaic.height, mosaic.width, 1)).all()  # genau der Ring
    # Mauerverband: Mindestens ein Stützteil überbrückt die Naht zwischen den Paneelen
    assert any(p.col < 16 < p.col + p.cols_spanned for p in mosaic.supports)


def test_corner_color(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(frame_width=2, frame_corner_color_id=15)
    mosaic = make_pipeline(pipeline_config).run(image_file, overrides).mosaic
    corners = [(0, 0), (1, 1), (0, 15), (15, 0), (14, 14)]
    assert {mosaic.color_at(row, col).name for row, col in corners} == {"White"}
    assert mosaic.color_at(0, 5).name == "Black"


def test_custom_size_from_aspect_ratio(pipeline_config: AppConfig, tmp_path: Path) -> None:
    wide_image = tmp_path / "quer.png"
    Image.new("RGB", (40, 20), (201, 26, 9)).save(wide_image)  # Seitenverhältnis 2:1
    overrides = MosaicOverrides(width_studs=42)
    mosaic = make_pipeline(pipeline_config).run(wide_image, overrides).mosaic
    # 42 rastet auf drei 16er-Grundplatten ein (48); Bildfläche 46 breit → 23 hoch plus
    # Rahmen = 25, rastet auf zwei Platten ein (32). Kein Teil des Unterbaus steht über.
    assert (mosaic.width, mosaic.height) == (48, 32)
    assert len(mosaic.substrate) == 3 * 2


def test_custom_size_without_substrate_is_exact(pipeline_config: AppConfig, tmp_path: Path) -> None:
    wide_image = tmp_path / "quer.png"
    Image.new("RGB", (40, 20), (201, 26, 9)).save(wide_image)  # Seitenverhältnis 2:1
    overrides = MosaicOverrides(substrate=Substrate.NONE, width_studs=42)
    mosaic = make_pipeline(pipeline_config).preview(wide_image, overrides)
    # Ohne Unterbau ist jede Größe baubar: Bildfläche 40 × 20 plus Rahmen → 42 × 22
    assert (mosaic.width, mosaic.height) == (42, 22)
    assert not mosaic.substrate


def test_preview_writes_no_files(pipeline_config: AppConfig, image_file: Path) -> None:
    pipeline = make_pipeline(pipeline_config)
    mosaic = pipeline.preview(image_file)
    assert mosaic.width == 16 and mosaic.surface
    assert not pipeline_config.output_dir.exists()
    info = pipeline.inspect_image(image_file)
    assert (info.width, info.height, info.format) == (32, 32, "PNG")


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


def test_unavailable_substrate_color(pipeline_config: AppConfig, image_file: Path) -> None:
    config = dataclasses.replace(
        pipeline_config, layout=dataclasses.replace(pipeline_config.layout, substrate_color_id=0)
    )
    with pytest.raises(ConfigError, match=r"Teil 3867 .* gibt es nicht in Farbe 0"):
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


# --- Stile und Relief ----------------------------------------------------------------


@pytest.fixture
def circle_file(tmp_path: Path) -> Path:
    """Roter Kreis auf Weiß: runde Kontur für die Formwahl."""
    path = tmp_path / "kreis.png"
    image = Image.new("RGB", (140, 140), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((20, 20, 120, 120), fill=(201, 26, 9))
    image.save(path)
    return path


def _coverage(mosaic: Mosaic) -> npt.NDArray[np.intp]:
    """Wie oft jede Zelle von einem sichtbaren Bildteil belegt ist."""
    counts = np.zeros((mosaic.height, mosaic.width), dtype=np.intp)
    for placement in mosaic.placements:
        for cell in placement.cells():
            counts[cell] += 1
    return counts


@pytest.mark.parametrize("style", ["formen", "punkte", "noppen"])
def test_styles_cover_every_image_cell_once(
    pipeline_config: AppConfig, circle_file: Path, style: str
) -> None:
    mosaic = make_pipeline(pipeline_config).preview(circle_file, MosaicOverrides(style=style))
    inner = ~frame_mask(mosaic.height, mosaic.width, 1)
    counts = _coverage(mosaic)
    assert (counts[inner] == 1).all() and (counts[~inner] == 0).all()
    shaped = [p for p in mosaic.placements if p.part.is_shaped]
    assert shaped
    if style != "formen":  # punkte und noppen: jede Noppe rund
        assert len(shaped) == inner.sum()
    assert mosaic.metadata["statistik"]["formteile"] == len(shaped)  # type: ignore[index]


def test_shape_underlays_sit_below_their_parts(
    pipeline_config: AppConfig, circle_file: Path
) -> None:
    mosaic = make_pipeline(pipeline_config).preview(circle_file, MosaicOverrides(style="formen"))
    under = {cell: p for p in mosaic.relief for cell in p.cells()}
    for part in (p for p in mosaic.placements if p.part.is_shaped and p.bottom_ldu):
        for cell in part.cells():
            assert under[cell].top_ldu == part.bottom_ldu


def test_relief_lifts_the_subject(pipeline_config: AppConfig, circle_file: Path) -> None:
    overrides = MosaicOverrides(relief=ReliefMode.SUBJECT, relief_levels=2)
    mosaic = make_pipeline(pipeline_config).preview(circle_file, overrides)
    raised = [p for p in mosaic.placements if p.bottom_ldu == 16]
    flat = [p for p in mosaic.placements if p.bottom_ldu == 0]
    assert raised and flat
    assert {p.color.id for p in flat} == {15}  # weißer Hintergrund bleibt unten
    assert {p.bottom_ldu for p in mosaic.relief} == {0, 8}  # zwei Stützlagen
    assert {p.color.id for p in mosaic.relief} == {15}  # in der Farbe des Unterbaus
    steps = assembly_steps(mosaic, section_studs=16, rows_per_step=4)
    image_steps = [step.kind for step in steps if step.section is not None]
    assert image_steps[:2] == [StepKind.RELIEF, StepKind.RELIEF]  # erst die Lagen, dann das Bild
    assert StepKind.SECTION in image_steps


def test_shaped_parts_in_ldraw_and_parts_list(
    pipeline_config: AppConfig, circle_file: Path
) -> None:
    result = make_pipeline(pipeline_config).run(circle_file, MosaicOverrides(style="punkte"))
    ldr = (result.target_dir / "mosaik.ldr").read_text(encoding="utf-8")
    assert "98138.dat" in ldr
    parts_list = (result.target_dir / "teileliste.csv").read_text(encoding="utf-8")
    assert "98138" in parts_list
