"""Tests für die Pipeline (offline, mit Mini-Katalog)."""

import dataclasses
import json
from pathlib import Path

import numpy as np
import numpy.typing as npt
import pytest
from conftest import make_pipeline
from PIL import Image, ImageDraw

from lego_mosaik.config import AppConfig, MosaicOverrides, OverrideInput, parse_overrides
from lego_mosaik.domain.editing import FillEdit, StudEdit
from lego_mosaik.domain.errors import ConfigError, DownloadError, NotFoundError
from lego_mosaik.domain.layout import frame_mask
from lego_mosaik.domain.models import (
    BackgroundMode,
    ColorCategory,
    Mosaic,
    PartKind,
    ReliefMode,
    Smoothing,
    Substrate,
)
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
    assert safe_name("Sonne über dem Meer") == "Sonne_ueber_dem_Meer"
    assert safe_name("😀") == "mosaik"


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
    assert mosaic.underlays
    under = {cell: p for p in mosaic.underlays for cell in p.cells()}
    for part in (p for p in mosaic.placements if p.part.is_shaped and p.bottom_ldu):
        for cell in part.cells():
            assert under[cell].top_ldu == part.bottom_ldu
    steps = assembly_steps(mosaic, section_studs=16, rows_per_step=4)
    kinds = [step.kind for step in steps if step.section is not None]
    # die Unterlagen sind ein eigener Schritt vor dem Bild, nicht Teil des Reliefs
    assert kinds.index(StepKind.UNDERLAY) < kinds.index(StepKind.SECTION)
    assert StepKind.RELIEF not in kinds


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


def test_relief_of_three_plates_uses_bricks(pipeline_config: AppConfig, circle_file: Path) -> None:
    overrides = MosaicOverrides(relief=ReliefMode.SUBJECT, relief_levels=3)
    mosaic = make_pipeline(pipeline_config).preview(circle_file, overrides)
    assert mosaic.relief
    # Motiv-Relief: alle erhöhten Zellen sind 3 Plates hoch → nur Bricks, eine Lage
    assert {p.part.kind for p in mosaic.relief} == {PartKind.BRICK}
    assert {p.bottom_ldu for p in mosaic.relief} == {0}
    assert {p.bottom_ldu for p in mosaic.placements} == {0, 24}


def _occupancy(mosaic: Mosaic) -> dict[tuple[int, int, int], object]:
    """(Zeile, Spalte, Plate-Lage) → Teil; schlägt fehl, wenn zwei Teile dieselbe Stelle belegen."""
    taken: dict[tuple[int, int, int], object] = {}
    for placement in mosaic.stacked:
        levels = range(placement.bottom_ldu // 8, placement.top_ldu // 8)
        for (row, col), level in ((cell, level) for cell in placement.cells() for level in levels):
            key = (row, col, level)
            assert key not in taken, f"{placement} überschneidet {taken[key]} bei {key}"
            taken[key] = placement
    return taken


@pytest.mark.parametrize(
    "overrides",
    [
        MosaicOverrides(style="formen"),
        MosaicOverrides(style="formen", relief=ReliefMode.SUBJECT, relief_levels=3),
        MosaicOverrides(style="formen", relief=ReliefMode.BRIGHT, relief_levels=2),
        MosaicOverrides(style="punkte", frame_raise_plates=1),
        MosaicOverrides(substrate=Substrate.PLATES, plates_wide=2, frame_raise_plates=2),
    ],
)
def test_every_part_has_its_own_place_and_stands_on_something(
    pipeline_config: AppConfig, circle_file: Path, overrides: MosaicOverrides
) -> None:
    mosaic = make_pipeline(pipeline_config).preview(circle_file, overrides)
    taken = _occupancy(mosaic)
    for placement in mosaic.stacked:
        level = placement.bottom_ldu // 8
        if level == 0:
            continue  # steht auf dem Unterbau
        for row, col in placement.cells():
            assert (row, col, level - 1) in taken, f"{placement} schwebt bei {(row, col)}"


def test_rare_colors_are_merged(pipeline_config: AppConfig, tmp_path: Path) -> None:
    # Weißes Bild mit drei einzelnen roten Pixeln
    pixels = np.full((14, 14, 3), 255, dtype=np.uint8)
    for row, col in ((2, 3), (7, 9), (11, 4)):
        pixels[row, col] = (201, 26, 9)
    path = tmp_path / "punkte.png"
    Image.fromarray(pixels).save(path)
    config = dataclasses.replace(
        pipeline_config, palette=dataclasses.replace(pipeline_config.palette, min_color_share=0.02)
    )
    mosaic = make_pipeline(config).preview(path, MosaicOverrides(style="quadrat", frame_width=1))
    assert "Red" not in {color.name for color in mosaic.used_colors()}
    kept = make_pipeline(pipeline_config).preview(path, MosaicOverrides(style="quadrat"))
    assert "Red" in {color.name for color in kept.used_colors()}


@pytest.mark.parametrize("style", ["quadrat", "formen"])
def test_majority_smoothing_avoids_edge_mix_colors(
    pipeline_config: AppConfig, tmp_path: Path, style: str
) -> None:
    # Gelb/Blau-Kante mitten durch eine Noppe (je 2 Pixel): gemittelt eine Mischfarbe
    pixels = np.full((56, 56, 3), (242, 205, 55), dtype=np.uint8)
    pixels[:, 30:] = (0, 85, 191)
    path = tmp_path / "kante.png"
    Image.fromarray(pixels).save(path)
    pipeline = make_pipeline(pipeline_config)

    def image_colors(smoothing: Smoothing) -> set[str]:
        overrides = MosaicOverrides(style=style, smoothing=smoothing, strength=0.0)
        mosaic = pipeline.preview(path, overrides)
        inner = ~frame_mask(mosaic.height, mosaic.width, 1)
        return {mosaic.palette[index].name for index in mosaic.color_grid[inner].tolist()}

    assert image_colors(Smoothing.NONE) - {"Yellow", "Blue"}
    assert image_colors(Smoothing.MEDIUM) == {"Yellow", "Blue"}


def test_color_count_matches_parts_list(pipeline_config: AppConfig, circle_file: Path) -> None:
    result = make_pipeline(pipeline_config).run(circle_file, MosaicOverrides(style="formen"))
    stats = result.stats
    rows = (result.target_dir / "teileliste.csv").read_text(encoding="utf-8-sig").splitlines()
    colors_in_list = {row.split(";")[2] for row in rows[1:]}
    assert stats["farben"] == len(colors_in_list) == len(result.mosaic.part_colors())
    assert stats["teile"] == len(result.mosaic.all_parts)
    assert stats["altfarben"] == 0


def test_shaped_parts_in_ldraw_and_parts_list(
    pipeline_config: AppConfig, circle_file: Path
) -> None:
    result = make_pipeline(pipeline_config).run(circle_file, MosaicOverrides(style="punkte"))
    ldr = (result.target_dir / "mosaik.ldr").read_text(encoding="utf-8")
    assert "98138.dat" in ldr
    parts_list = (result.target_dir / "teileliste.csv").read_text(encoding="utf-8")
    assert "98138" in parts_list


def test_large_plates_are_bonded_across_every_seam(
    pipeline_config: AppConfig, image_file: Path
) -> None:
    overrides = MosaicOverrides(substrate=Substrate.PLATES, plates_wide=2, plates_high=2)
    mosaic = make_pipeline(pipeline_config).preview(image_file, overrides)
    assert len(mosaic.substrate) == 4 and mosaic.bond
    owner = {cell: index for index, part in enumerate(mosaic.bond) for cell in part.cells()}
    assert len(owner) == mosaic.width * mosaic.height  # die Lage deckt alles ab
    seam = 16
    for line in range(mosaic.height):
        # senkrechte Naht: links und rechts davon liegt dieselbe Plate
        assert owner[line, seam - 1] == owner[line, seam], f"Zeile {line}"
        assert owner[seam - 1, line] == owner[seam, line], f"Spalte {line}"
    assert all(p.bottom_ldu >= 8 for p in mosaic.placements + mosaic.frame)
    steps = [step.kind for step in assembly_steps(mosaic, section_studs=16, rows_per_step=4)]
    assert steps[:2] == [StepKind.SUBSTRATE, StepKind.BOND]


# --- Hintergrund, eigene Palette, Bearbeiten --------------------------------------------


def _disk_image(path: Path) -> Path:
    """Weißer Hintergrund mit roter Scheibe in der Mitte (64 × 64)."""
    image = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(image).ellipse((16, 16, 48, 48), fill=(201, 26, 9))
    image.save(path)
    return path


def test_background_is_replaced_by_one_lego_color(
    pipeline_config: AppConfig, tmp_path: Path
) -> None:
    image = _disk_image(tmp_path / "scheibe.png")
    overrides = MosaicOverrides(background=BackgroundMode.REMOVE, background_color_id=1)
    mosaic = make_pipeline(pipeline_config).preview(image, overrides)
    assert mosaic.color_at(1, 1).name == "Blue"  # Ecke der Bildfläche (Rahmen = 1)
    assert mosaic.color_at(8, 8).name == "Red"  # Motiv bleibt


def test_background_color_must_exist_as_single_stud(
    pipeline_config: AppConfig, tmp_path: Path
) -> None:
    overrides = MosaicOverrides(background=BackgroundMode.REMOVE, background_color_id=2)
    with pytest.raises(ConfigError, match="Hintergrundfarbe Green"):
        make_pipeline(pipeline_config).preview(_disk_image(tmp_path / "s.png"), overrides)


def test_relief_subject_uses_detected_background(
    pipeline_config: AppConfig, tmp_path: Path
) -> None:
    overrides = MosaicOverrides(
        background=BackgroundMode.LIGHTEN, relief=ReliefMode.SUBJECT, relief_levels=1
    )
    mosaic = make_pipeline(pipeline_config).preview(_disk_image(tmp_path / "s.png"), overrides)

    def bottom(row: int, col: int) -> int:
        return next(p.bottom_ldu for p in mosaic.placements if (row, col) in set(p.cells()))

    assert mosaic.relief  # Stützteile unter dem Motiv
    assert bottom(8, 8) > bottom(1, 1)  # Motiv höher als der (aufgehellte) Hintergrund


def test_only_color_ids_restrict_the_palette(pipeline_config: AppConfig, image_file: Path) -> None:
    overrides = MosaicOverrides(only_color_ids=(0, 15))
    mosaic = make_pipeline(pipeline_config).preview(image_file, overrides)
    assert {color.id for color in mosaic.used_colors()} <= {0, 15}


def test_run_keeps_source_and_edit_creates_a_new_mosaic(
    pipeline_config: AppConfig, image_file: Path
) -> None:
    pipeline = make_pipeline(pipeline_config)
    inputs = OverrideInput(frame_width=2)
    first = pipeline.run(image_file, parse_overrides(inputs), title="bild", inputs=inputs)
    assert (first.target_dir / "original.png").read_bytes() == image_file.read_bytes()
    saved = json.loads((first.target_dir / "eingaben.json").read_text(encoding="utf-8"))
    assert saved["eingaben"]["frame_width"] == 2 and saved["aenderungen"] == []
    assert pipeline.inputs_of(first.target_dir).frame_width == 2

    second = pipeline.edit(first.target_dir, [StudEdit(3, 3, 15), FillEdit(14, 14, 4)])
    assert second.id != first.id and second.mosaic.width == first.mosaic.width
    assert second.mosaic.color_at(2, 2).name == "White"
    result = json.loads((second.target_dir / "ergebnis.json").read_text(encoding="utf-8"))
    assert result["basis"] == first.id
    third = pipeline.edit(second.target_dir, [StudEdit(4, 4, 0)])
    saved = json.loads((third.target_dir / "eingaben.json").read_text(encoding="utf-8"))
    assert len(saved["aenderungen"]) == 3  # Änderungen sammeln sich
    assert third.mosaic.color_at(2, 2).name == "White"


def test_edit_needs_saved_inputs(pipeline_config: AppConfig, image_file: Path) -> None:
    config = dataclasses.replace(
        pipeline_config, storage=dataclasses.replace(pipeline_config.storage, keep_source=False)
    )
    result = make_pipeline(config).run(image_file)
    assert not (result.target_dir / "eingaben.json").exists()
    with pytest.raises(NotFoundError, match="ohne Originalbild"):
        make_pipeline(config).edit(result.target_dir, [StudEdit(2, 2, 0)])


def test_detect_background_suggests_a_palette_color(
    pipeline_config: AppConfig, tmp_path: Path
) -> None:
    result = make_pipeline(pipeline_config).detect_background(
        _disk_image(tmp_path / "s.png"), max_px=32
    )
    assert result.report.color == (255, 255, 255)
    assert result.lego_color.name == "White"
    assert result.report.mask_png.startswith(b"\x89PNG")


def test_colors_reject_unknown_ids(pipeline_config: AppConfig) -> None:
    pipeline = make_pipeline(pipeline_config)
    assert [c.name for c in pipeline.colors([0, 15])] == ["Black", "White"]
    with pytest.raises(ConfigError, match="Unbekannte Farb-IDs: 999"):
        pipeline.colors([0, 999])
