"""Tests für die Bauanleitung (PNG-Seiten und PDF)."""

import dataclasses
import re
from pathlib import Path

import numpy as np
import pytest
from conftest import make_pipeline
from PIL import Image

from lego_mosaik.config import AppConfig, MosaicOverrides
from lego_mosaik.domain.models import Mosaic, PartKind, Substrate
from lego_mosaik.domain.sections import StepKind, assembly_steps
from lego_mosaik.outputs.instructions_png import (
    PAGE_BREAK,
    InstructionsWriter,
    PageLayout,
    _DocumentBuilder,
    build_pages,
    paginate,
)
from lego_mosaik.outputs.iso_render import HIGHLIGHT_RGB, Area
from lego_mosaik.outputs.plans import connection_plan

LAYOUT = PageLayout(
    width=600,
    height=900,
    margin=20,
    max_stud_px=24,
    section_studs=8,
    rows_per_step=4,
    step_render_height=300,
)


@pytest.fixture
def mosaic(pipeline_config: AppConfig, image_file: Path) -> Mosaic:
    return make_pipeline(pipeline_config).run(image_file).mosaic  # 16 × 16 Noppen, 1 Grundplatte


def test_document_has_cover_inventory_and_steps(mosaic: Mosaic) -> None:
    pages = build_pages(mosaic, LAYOUT)
    # mindestens: Titel, Teileübersicht, Grundplatten, 4 Abschnitte à eigener Seite
    assert len(pages) >= 7
    assert all(page.width == LAYOUT.width and page.height >= LAYOUT.height for page in pages)


def test_writer_creates_pngs_and_pdf(mosaic: Mosaic, tmp_path: Path) -> None:
    pdf = InstructionsWriter(LAYOUT).write(mosaic, tmp_path)
    pngs = sorted(tmp_path.glob("anleitung_*.png"))
    assert pdf.name == "anleitung.pdf" and pngs[0].name == "anleitung_01.png"
    # Jede PDF-Seite ist ein Objekt "/Type /Page" (ohne "s" dahinter)
    assert len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes())) == len(pngs)


def test_without_substrate(mosaic: Mosaic) -> None:
    with_plate = build_pages(mosaic, LAYOUT)
    without = build_pages(dataclasses.replace(mosaic, substrate=()), LAYOUT)
    assert len(without) == len(with_plate) - 1  # die Grundplatten-Seite entfällt


def test_paginate_keeps_units_together_and_breaks_pages() -> None:
    block = Image.new("RGB", (100, 400), (255, 255, 255))
    # zwei Blöcke à 400 passen (mit Abstand) auf 860 Pixel Inhaltshöhe, ein dritter nicht
    pages = paginate([(block,), (block,), (block,), PAGE_BREAK, (block,)], LAYOUT)
    assert len(pages) == 3
    tall = paginate([(block, block, block)], LAYOUT)  # eine Einheit, höher als die Seite
    assert len(tall) == 1 and tall[0].height > LAYOUT.height


@pytest.fixture
def art_mosaic(pipeline_config: AppConfig, image_file: Path) -> Mosaic:
    """32 × 16 Noppen auf zwei Art-Paneelen mit erhöhtem Tile-Rahmen."""
    overrides = MosaicOverrides(
        substrate=Substrate.ART_PANEL,
        substrate_color_id=0,
        plates_wide=2,
        plates_high=1,
        frame_part_kinds=(PartKind.TILE,),
        frame_raise_plates=1,
    )
    return make_pipeline(pipeline_config).run(image_file, overrides).mosaic


def test_assembly_order_like_lego_art(art_mosaic: Mosaic) -> None:
    steps = assembly_steps(art_mosaic, section_studs=8, rows_per_step=4)
    kinds = [step.kind for step in steps]
    assert kinds[:4] == [
        StepKind.SUBSTRATE,
        StepKind.CONNECTORS,
        StepKind.FRAME_SUPPORT,
        StepKind.FRAME,
    ]
    assert set(kinds[4:]) == {StepKind.SECTION}
    # Jedes Teil kommt in genau einem Schritt vor
    assert sum(len(step.placements) for step in steps) == len(art_mosaic.all_parts)


def test_art_document_renders(art_mosaic: Mosaic, tmp_path: Path) -> None:
    pdf = InstructionsWriter(LAYOUT).write(art_mosaic, tmp_path)
    pngs = sorted(tmp_path.glob("anleitung_*.png"))
    # Titel, Teile, Paneele, Pins, Rahmen-Unterbau, Rahmen, 8 Abschnitte, Fertig
    assert len(pngs) >= 14
    assert len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes())) == len(pngs)


def test_connection_plan_marks_pins(art_mosaic: Mosaic) -> None:
    font = LAYOUT.font(18)
    plan = connection_plan(art_mosaic.substrate, art_mosaic.connectors, stud_px=10, font=font)
    assert plan.size == (32 * 10, 16 * 10)
    pixels = np.asarray(plan).reshape(-1, 3)
    assert (pixels == HIGHLIGHT_RGB).all(axis=1).any()  # Pins orange eingezeichnet


def test_overview_names_real_step_numbers(art_mosaic: Mosaic) -> None:
    builder = _DocumentBuilder(art_mosaic, LAYOUT)
    order = builder.build_order()
    assert order[0] == ("Schritt 1", "2 LEGO-Art-Paneele 16 × 16 auslegen")
    assert order[1][0] == "Schritt 2"
    assert order[2] == ("Schritte 3–4", "Rahmen bauen")  # Rahmen-Unterbau und Rahmen
    assert order[3][0] == f"Schritte 5–{len(builder.steps)}"
    # derselbe Name im ersten Bauschritt wie auf der Übersicht
    assert builder.base_title(builder.steps[0]) == order[0][1]


def test_section_subtitles_do_not_overlap(art_mosaic: Mosaic) -> None:
    builder = _DocumentBuilder(art_mosaic, LAYOUT)
    first = [step for step in builder.steps if step.section and step.section.number == 1]
    subtitles = [builder.section_subtitle(step, step.section) for step in first if step.section]
    count = len(first)
    assert subtitles == [f"Abschnitt 1/8 · Teil {n} von {count}" for n in range(1, count + 1)]


def test_step_view_is_top_down_with_ruler(art_mosaic: Mosaic) -> None:
    builder = _DocumentBuilder(art_mosaic, LAYOUT)
    step = next(step for step in builder.steps if step.kind is StepKind.SECTION)
    assert step.section is not None
    area = Area(step.section.row, step.section.col, step.section.rows, step.section.cols)
    view = builder.top_view(step.placements, step.placements, area, 500, 500, 1)
    # Draufsicht: quadratische Abschnitte bleiben quadratisch (plus Lineal oben und links)
    assert abs(view.width - view.height) <= 1
    pixels = np.asarray(view).reshape(-1, 3)
    assert (pixels == HIGHLIGHT_RGB).all(axis=1).any()  # neue Teile orange umrandet
