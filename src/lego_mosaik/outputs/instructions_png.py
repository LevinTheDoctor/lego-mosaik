"""Bauanleitung als PNG-Seiten und mehrseitiges PDF (nur mit Pillow).

Aufbau:

1. Übersicht: ganzes Mosaik mit eingezeichneten Abschnitten, Grundplatten, Kennzahlen
2. Farbliste: Kennung (A, B, …), Farbfeld, Name, LEGO-Name, BrickLink-Code, Noppenzahl
3. Pro Abschnitt eine Seite: Teilekanten, Farbkennung in jeder Noppe, benötigte Teile

Die Abschnitte haben dieselbe Reihenfolge wie die Bauschritte in ``mosaik.ldr``.
"""

import functools
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

lazy from PIL import Image, ImageDraw, ImageFont

from lego_mosaik.domain.models import LegoColor, Mosaic, PartsList, Placement
from lego_mosaik.domain.sections import Section, placements_in, split_sections
from lego_mosaik.outputs.rendering import Region, render_placements, text_color

logger = logging.getLogger(__name__)

PDF_FILE_NAME = "anleitung.pdf"
PNG_PATTERN = "anleitung_{number:02d}.png"
PDF_RESOLUTION_DPI = 150.0
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
SECTION_LINE_RGB = (220, 30, 30)
SECTION_LINE_WIDTH = 3
TITLE_SIZE, HEADING_SIZE, BODY_SIZE = 40, 30, 22
BLOCK_SPACING = 18  # Abstand zwischen Blöcken in Pixeln
LINE_SPACING = 1.35  # Zeilenhöhe als Vielfaches der Schriftgröße
ALPHABET_SIZE = 26

type Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
type Block = Image.Image


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Seitengröße und Ränder in Pixeln, Abschnittsgröße in Noppen."""

    width: int
    height: int
    margin: int
    max_stud_px: int
    section_studs: int
    font_paths: tuple[Path, ...] = ()

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin

    def font(self, size: int) -> Font:
        """Erste vorhandene TrueType-Schrift; sonst Pillows Standardschrift (ohne Umlaute)."""
        return _load_font(self.font_paths, size)


class InstructionsWriter:
    """Schreibt ``anleitung_01.png`` … und ``anleitung.pdf``.

    Args:
        layout: Seitengröße, Ränder, größte Noppe und Abschnittsgröße.
    """

    name = "instructions_png"

    def __init__(self, layout: PageLayout) -> None:
        self._layout = layout

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Erzeugt alle Seiten, speichert sie als PNG und zusätzlich als ein PDF."""
        pages = build_pages(mosaic, self._layout)
        for number, page in enumerate(pages, start=1):
            page.save(target_dir / PNG_PATTERN.format(number=number), format="PNG")
        pdf_path = target_dir / PDF_FILE_NAME
        # save_all + append_images: Pillow schreibt alle Bilder als Seiten in eine PDF-Datei
        pages[0].save(
            pdf_path,
            format="PDF",
            save_all=True,
            append_images=pages[1:],
            resolution=PDF_RESOLUTION_DPI,
        )
        logger.info("Bauanleitung geschrieben: %d Seiten, %s", len(pages), pdf_path)
        return pdf_path


def build_pages(mosaic: Mosaic, layout: PageLayout) -> list[Image.Image]:
    """Alle Seiten der Anleitung in Reihenfolge."""
    sections = split_sections(mosaic.width, mosaic.height, layout.section_studs)
    codes = color_codes(mosaic.used_colors())
    pages = [_overview_page(mosaic, sections, layout), _color_page(mosaic, codes, layout)]
    pages += [_section_page(mosaic, section, len(sections), codes, layout) for section in sections]
    return pages


def color_codes(colors: Sequence[LegoColor]) -> dict[int, str]:
    """Kurze Kennungen A, B, …, Z, AA, AB, … pro Farb-ID (in der gegebenen Reihenfolge)."""
    return {color.id: _letters(index) for index, color in enumerate(colors)}


def _letters(index: int) -> str:
    """0 → A, 25 → Z, 26 → AA (wie Spaltennamen in einer Tabellenkalkulation)."""
    letters = ""
    number = index + 1
    while number:
        # divmod liefert Quotient und Rest; -1, weil es keine "Null"-Ziffer gibt
        number, remainder = divmod(number - 1, ALPHABET_SIZE)
        letters = chr(ord("A") + remainder) + letters
    return letters


# --- Seiten ------------------------------------------------------------------------------


def _overview_page(mosaic: Mosaic, sections: Sequence[Section], layout: PageLayout) -> Image.Image:
    """Seite 1: Gesamtbild mit Abschnittsgrenzen und Kennzahlen."""
    body = layout.font(BODY_SIZE)
    parts = PartsList.from_placements(mosaic.placements).total_quantity
    lines = [
        f"Größe: {mosaic.width} × {mosaic.height} Noppen, {len(sections)} Abschnitte",
        f"Teile: {parts} (ohne Grundplatten), Farben: {len(mosaic.used_colors())}",
        *_baseplate_lines(mosaic.baseplates),
        "Gebaut wird Abschnitt für Abschnitt in der Reihenfolge der Nummern.",
    ]
    blocks = [
        _text_block(f"Bauanleitung: {mosaic.title}", layout.font(TITLE_SIZE), layout.content_width),
        _overview_image(mosaic, sections, layout),
        *(_text_block(line, body, layout.content_width) for line in lines),
    ]
    return _compose(blocks, layout)


def _baseplate_lines(baseplates: Sequence[Placement]) -> list[str]:
    """Hinweis auf die Grundplatten (Schritt 0)."""
    if not baseplates:
        return []
    first = baseplates[0]
    return [
        f"Schritt 0: {len(baseplates)} × {first.part.name} in {first.color.name} "
        "nebeneinander auslegen."
    ]


def _color_page(mosaic: Mosaic, codes: dict[int, str], layout: PageLayout) -> Image.Image:
    """Seite 2: Liste aller Farben mit Kennung."""
    body = layout.font(BODY_SIZE)
    studs = Counter[int]()
    for placement in mosaic.placements:
        studs[placement.color.id] += placement.part.area
    blocks = [_text_block("Farben", layout.font(HEADING_SIZE), layout.content_width)]
    for color in mosaic.used_colors():
        details = [color.name]
        if color.lego_name:
            details.append(f"LEGO: {color.lego_name}")
        if color.bricklink_code is not None:
            details.append(f"BrickLink {color.bricklink_code}")
        details.append(f"{studs[color.id]} Noppen")
        blocks.append(_legend_row(color, codes[color.id], " · ".join(details), body, layout))
    return _compose(blocks, layout)


def _section_page(
    mosaic: Mosaic, section: Section, total: int, codes: dict[int, str], layout: PageLayout
) -> Image.Image:
    """Eine Seite pro Abschnitt: Ausschnitt mit Kennungen und die benötigten Teile."""
    placements = placements_in(section, mosaic.placements)
    stud_px = min(layout.max_stud_px, layout.content_width // section.cols)
    region = Region(section.row, section.col, section.rows, section.cols)
    heading = (
        f"Abschnitt {section.number} von {total} – Zeilen {section.row + 1}–"
        f"{section.row + section.rows}, Spalten {section.col + 1}–{section.col + section.cols}"
    )
    blocks = [
        _text_block(heading, layout.font(HEADING_SIZE), layout.content_width),
        render_placements(
            mosaic.placements, region=region, stud_px=stud_px, draw_part_edges=True, labels=codes
        ),
        *_section_legend(placements, codes, layout),
    ]
    return _compose(blocks, layout)


def _section_legend(
    placements: Sequence[Placement], codes: dict[int, str], layout: PageLayout
) -> list[Block]:
    """Pro Farbe eine Zeile: Kennung, Name und benötigte Teile mit Stückzahl."""
    body = layout.font(BODY_SIZE)
    entries = PartsList.from_placements(placements).entries
    # Sortierung nach (Länge, Text), damit "B" vor "AB" kommt wie in der Farbliste
    colors = sorted(
        {entry.color for entry in entries},
        key=lambda color: (len(codes[color.id]), codes[color.id]),
    )
    rows = []
    for color in colors:
        parts = ", ".join(
            f"{entry.quantity}× {entry.part.name}" for entry in entries if entry.color == color
        )
        rows.append(_legend_row(color, codes[color.id], f"{color.name}: {parts}", body, layout))
    return rows


# --- Bausteine ---------------------------------------------------------------------------


def _overview_image(mosaic: Mosaic, sections: Sequence[Section], layout: PageLayout) -> Block:
    """Gesamtbild mit roten Abschnittsgrenzen und großen Abschnittsnummern."""
    stud_px = max(1, min(layout.max_stud_px, layout.content_width // mosaic.width))
    image = render_placements(
        mosaic.placements,
        region=Region(0, 0, mosaic.height, mosaic.width),
        stud_px=stud_px,
        draw_part_edges=stud_px >= layout.max_stud_px // 3,
    )
    draw = ImageDraw.Draw(image)
    font = layout.font(max(BODY_SIZE, layout.section_studs * stud_px // 3))
    for section in sections:
        box = (
            section.col * stud_px,
            section.row * stud_px,
            (section.col + section.cols) * stud_px - 1,
            (section.row + section.rows) * stud_px - 1,
        )
        draw.rectangle(box, outline=SECTION_LINE_RGB, width=SECTION_LINE_WIDTH)
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        # stroke_*: weiße Schrift mit schwarzem Rand ist auf jeder Farbe lesbar
        draw.text(
            center,
            str(section.number),
            fill=WHITE,
            font=font,
            anchor="mm",
            stroke_width=3,
            stroke_fill=BLACK,
        )
    return image


def _legend_row(color: LegoColor, code: str, text: str, font: Font, layout: PageLayout) -> Block:
    """Farbfeld mit Kennung links, umbrochener Text rechts daneben."""
    line_height = round(BODY_SIZE * LINE_SPACING)
    swatch_size = line_height + BLOCK_SPACING // 2
    text_block = _text_block(text, font, layout.content_width - swatch_size - BLOCK_SPACING)
    row = Image.new("RGB", (layout.content_width, max(swatch_size, text_block.height)), WHITE)
    draw = ImageDraw.Draw(row)
    draw.rectangle((0, 0, swatch_size - 1, swatch_size - 1), fill=color.rgb, outline=BLACK)
    draw.text(
        (swatch_size / 2, swatch_size / 2), code, fill=text_color(color.rgb), font=font, anchor="mm"
    )
    row.paste(text_block, (swatch_size + BLOCK_SPACING, 0))
    return row


def _text_block(text: str, font: Font, width: int) -> Block:
    """Text als Bild, an Wortgrenzen auf ``width`` Pixel umbrochen."""
    lines = _wrap(text, font, width)
    size = font.size if isinstance(font, ImageFont.FreeTypeFont) else BODY_SIZE
    line_height = round(size * LINE_SPACING)
    block = Image.new("RGB", (width, line_height * len(lines)), WHITE)
    draw = ImageDraw.Draw(block)
    for index, line in enumerate(lines):
        draw.text((0, index * line_height), line, fill=BLACK, font=font)
    return block


def _wrap(text: str, font: Font, width: int) -> list[str]:
    """Greedy-Zeilenumbruch: so viele Wörter pro Zeile, wie in ``width`` Pixel passen."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        # getlength misst die tatsächliche Breite des Texts in Pixeln
        if current and font.getlength(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    return [*lines, current] if current else lines or [""]


def _compose(blocks: Sequence[Block], layout: PageLayout) -> Image.Image:
    """Stapelt Blöcke untereinander; die Seite wächst, falls der Inhalt nicht passt."""
    content_height = sum(block.height for block in blocks) + BLOCK_SPACING * (len(blocks) - 1)
    page = Image.new(
        "RGB", (layout.width, max(layout.height, content_height + 2 * layout.margin)), WHITE
    )
    top = layout.margin
    for block in blocks:
        page.paste(block, (layout.margin, top))
        top += block.height + BLOCK_SPACING
    return page


@functools.cache
def _load_font(paths: tuple[Path, ...], size: int) -> Font:
    """Lädt die Schrift einmal pro Größe (``functools.cache`` merkt sich das Ergebnis)."""
    for path in paths:
        if path.is_file():
            return ImageFont.truetype(path, size)
    logger.warning(
        "Keine Schrift aus InstructionsConfig.font_paths gefunden; Umlaute fehlen in der Anleitung."
    )
    return ImageFont.load_default(size=size)
