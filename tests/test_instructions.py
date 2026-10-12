"""Tests für die Bauanleitung (PNG-Seiten und PDF)."""

import dataclasses
import re
from pathlib import Path

import pytest
from conftest import make_pipeline
from PIL import Image

from lego_mosaik.config import AppConfig
from lego_mosaik.domain.models import Mosaic
from lego_mosaik.outputs.instructions_png import (
    PAGE_BREAK,
    InstructionsWriter,
    PageLayout,
    build_pages,
    paginate,
)

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


def test_without_baseplate(mosaic: Mosaic) -> None:
    with_plate = build_pages(mosaic, LAYOUT)
    without = build_pages(dataclasses.replace(mosaic, baseplates=()), LAYOUT)
    assert len(without) == len(with_plate) - 1  # die Grundplatten-Seite entfällt


def test_paginate_keeps_units_together_and_breaks_pages() -> None:
    block = Image.new("RGB", (100, 400), (255, 255, 255))
    # zwei Blöcke à 400 passen (mit Abstand) auf 860 Pixel Inhaltshöhe, ein dritter nicht
    pages = paginate([(block,), (block,), (block,), PAGE_BREAK, (block,)], LAYOUT)
    assert len(pages) == 3
    tall = paginate([(block, block, block)], LAYOUT)  # eine Einheit, höher als die Seite
    assert len(tall) == 1 and tall[0].height > LAYOUT.height
