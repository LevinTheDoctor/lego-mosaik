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
    InstructionsWriter,
    PageLayout,
    build_pages,
    color_codes,
)

LAYOUT = PageLayout(width=600, height=800, margin=20, max_stud_px=24, section_studs=8)


@pytest.fixture
def mosaic(pipeline_config: AppConfig, image_file: Path) -> Mosaic:
    return make_pipeline(pipeline_config).run(image_file).mosaic  # 16 × 16 Noppen


def test_page_count_is_overview_colors_and_sections(mosaic: Mosaic) -> None:
    pages = build_pages(mosaic, LAYOUT)
    assert len(pages) == 2 + 4  # Übersicht, Farbliste, 4 Abschnitte à 8 × 8
    assert all(page.width == LAYOUT.width and page.height >= LAYOUT.height for page in pages)


def test_writer_creates_pngs_and_pdf(mosaic: Mosaic, tmp_path: Path) -> None:
    pdf = InstructionsWriter(LAYOUT).write(mosaic, tmp_path)
    assert pdf.name == "anleitung.pdf"
    pngs = sorted(tmp_path.glob("anleitung_*.png"))
    assert [p.name for p in pngs[:2]] == ["anleitung_01.png", "anleitung_02.png"]
    # Jede PDF-Seite ist ein Objekt "/Type /Page" (ohne "s" dahinter)
    assert len(re.findall(rb"/Type\s*/Page\b", pdf.read_bytes())) == len(pngs)
    with Image.open(pngs[0]) as first:
        assert first.size[0] == LAYOUT.width


def test_long_legend_makes_page_taller(mosaic: Mosaic) -> None:
    tiny = dataclasses.replace(LAYOUT, height=100)
    assert all(page.height > 100 for page in build_pages(mosaic, tiny))


def test_color_codes_continue_after_z(mosaic: Mosaic) -> None:
    colors = [dataclasses.replace(mosaic.palette[0], id=i) for i in range(28)]
    codes = color_codes(colors)
    assert (codes[0], codes[25], codes[26], codes[27]) == ("A", "Z", "AA", "AB")
