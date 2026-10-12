"""Bauanleitung im LEGO-Stil als PNG-Seiten und mehrseitiges PDF (nur mit Pillow).

Aufbau:

1. Titelseite: gerendertes Mosaik (3D), Abschnittskarte und Kennzahlen
2. Teileübersicht: alle Teile als gerenderte Einzelteile mit Stückzahl
3. Schritt 1: Grundplatten auslegen (falls vorhanden)
4. Pro Bauschritt: Teile-Kasten („3×“ unter jedem Teilbild) und 3D-Ansicht des
   Abschnitts; neue Teile sind orange umrandet

Die Schritte sind exakt die der LDraw-Datei (``domain.sections.build_steps``).
Seiten werden automatisch gefüllt: Was zusammengehört (ein Schritt), bleibt auf
einer Seite; passen zwei Schritte auf eine Seite, stehen sie untereinander.
"""

import functools
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

lazy from PIL import Image, ImageDraw, ImageFont

from lego_mosaik.domain.models import (
    LegoColor,
    Mosaic,
    PartKind,
    PartsList,
    PartsListEntry,
    PartType,
    Placement,
    RgbTuple,
)
from lego_mosaik.domain.sections import BuildStep, Section, build_steps, split_sections
from lego_mosaik.outputs.iso_render import Area, IsoView, RenderCell, render_iso
from lego_mosaik.outputs.rendering import Region, render_placements

logger = logging.getLogger(__name__)

PDF_FILE_NAME = "anleitung.pdf"
PNG_PATTERN = "anleitung_{number:02d}.png"
PDF_RESOLUTION_DPI = 150.0
PNG_COMPRESS_LEVEL = 1
WHITE: RgbTuple = (255, 255, 255)
BLACK: RgbTuple = (0, 0, 0)
GRAY_TEXT: RgbTuple = (90, 90, 90)
CALLOUT_FILL: RgbTuple = (226, 238, 248)  # hellblauer Teile-Kasten wie bei LEGO
CALLOUT_BORDER: RgbTuple = (120, 160, 200)
MAP_HIGHLIGHT: RgbTuple = (220, 30, 30)
MAP_HIGHLIGHT_WIDTH = 4
TITLE_SIZE, STEP_SIZE, HEADING_SIZE, BODY_SIZE, SMALL_SIZE = 44, 56, 30, 22, 18
BLOCK_SPACING = 18
LINE_SPACING = 1.35
ICON_SCALE = 1.4  # Pixel pro LDU für Teilbilder (alle Teile im selben Maßstab)
ICON_MAX_PX = 220  # große Teile (Grundplatten) werden auf diese Größe eingepasst
ICON_GAP = 22
CALLOUT_PADDING = 16
COVER_MAP_PX = 420  # Abschnittskarte auf der Titelseite
INTRO_MAP_PX = 240  # kleinere Karte am Anfang jedes Abschnitts
COVER_RENDER_HEIGHT = 900
LARGE_RENDER_CELLS = 4096  # ab so vielen Zellen ohne Supersampling (schneller)

type Font = ImageFont.FreeTypeFont | ImageFont.ImageFont
type Block = Image.Image
type Unit = tuple[Block, ...]  # Blöcke, die auf derselben Seite bleiben


class PageBreak:
    """Markiert einen Seitenumbruch im Dokument."""


PAGE_BREAK = PageBreak()


@dataclass(frozen=True, slots=True)
class PageLayout:
    """Seiten-, Schritt- und Kameraeinstellungen der Anleitung (Größen in Pixeln)."""

    width: int
    height: int
    margin: int
    max_stud_px: int
    section_studs: int
    rows_per_step: int = 4
    view: IsoView = field(default_factory=IsoView)
    supersample: int = 2
    step_render_height: int = 820
    font_paths: tuple[Path, ...] = ()

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin

    @property
    def content_height(self) -> int:
        return self.height - 2 * self.margin

    def font(self, size: int) -> Font:
        """Erste vorhandene TrueType-Schrift; sonst Pillows Standardschrift (ohne Umlaute)."""
        return _load_font(self.font_paths, size)


class InstructionsWriter:
    """Schreibt ``anleitung_01.png`` … und ``anleitung.pdf``."""

    name = "instructions_png"

    def __init__(self, layout: PageLayout) -> None:
        self._layout = layout

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Erzeugt alle Seiten, speichert sie als PNG und zusätzlich als ein PDF."""
        pages = build_pages(mosaic, self._layout)
        for number, page in enumerate(pages, start=1):
            # compress_level=1: kaum größere Dateien, aber ein Vielfaches schneller als Stufe 6
            path = target_dir / PNG_PATTERN.format(number=number)
            page.save(path, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
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
    """Baut das ganze Dokument und verteilt es auf Seiten."""
    return paginate(_DocumentBuilder(mosaic, layout).document(), layout)


# --- Dokument --------------------------------------------------------------------------


class _DocumentBuilder:
    """Erzeugt die Einheiten der Anleitung in Lesereihenfolge (Builder Pattern)."""

    def __init__(self, mosaic: Mosaic, layout: PageLayout) -> None:
        self.mosaic = mosaic
        self.layout = layout
        self.sections = split_sections(mosaic.width, mosaic.height, layout.section_studs)
        self.steps = build_steps(
            mosaic.placements,
            width=mosaic.width,
            height=mosaic.height,
            section_studs=layout.section_studs,
            rows_per_step=layout.rows_per_step,
        )
        # Jede Platzierung bekommt eine Nummer; daran erkennt der Renderer Teilgrenzen
        self.part_ids = {placement: index for index, placement in enumerate(mosaic.placements)}
        self.icons = _IconCache(layout)
        self.baseplate_rgb = mosaic.baseplates[0].color.rgb if mosaic.baseplates else None
        self._maps: dict[int, tuple[Block, int]] = {}

    def document(self) -> list[Unit | PageBreak]:
        """Alle Einheiten mit Seitenumbrüchen vor Teileübersicht, Schritten und Abschnitten."""
        doc: list[Unit | PageBreak] = [self.cover(), PAGE_BREAK, *self.inventory(), PAGE_BREAK]
        first_number = 1
        if self.mosaic.baseplates:
            doc += [self.baseplate_step(), PAGE_BREAK]
            first_number = 2
        built: list[Placement] = []
        previous_section = 0
        for number, step in enumerate(self.steps, start=first_number):
            if step.section.number != previous_section:
                if built:
                    doc.append(PAGE_BREAK)  # jeder Abschnitt beginnt auf einer neuen Seite
                doc.append(self.section_intro(step.section))
                previous_section = step.section.number
            doc.append(self.step_unit(number, step, built))
            built += step.placements
        return doc

    def cover(self) -> Unit:
        """Titel, 3D-Bild des fertigen Mosaiks, Kennzahlen und Abschnittskarte."""
        mosaic, layout = self.mosaic, self.layout
        area = Area(0, 0, mosaic.height, mosaic.width)
        large = mosaic.width * mosaic.height > LARGE_RENDER_CELLS
        render = render_iso(
            self.cells(mosaic.placements, area),
            area,
            layout.view,
            baseplate_rgb=self.baseplate_rgb,
            max_width=layout.content_width,
            max_height=COVER_RENDER_HEIGHT,
            supersample=1 if large else layout.supersample,
        )
        total_steps = len(self.steps) + (1 if mosaic.baseplates else 0)
        parts = PartsList.from_placements(mosaic.placements).total_quantity
        facts = (
            f"{mosaic.width} × {mosaic.height} Noppen · {len(self.sections)} Abschnitte · "
            f"{total_steps} Schritte · {parts} Teile · {len(mosaic.used_colors())} Farben"
        )
        return (
            _text_block(mosaic.title, layout.font(TITLE_SIZE), layout.content_width),
            render,
            _text_block(facts, layout.font(BODY_SIZE), layout.content_width),
            self.section_map(None, COVER_MAP_PX),
        )

    def inventory(self) -> list[Unit]:
        """Teileübersicht: Überschrift, dann Zeilen mit Teilbildern (jede Zeile eine Einheit)."""
        entries = PartsList.from_placements(self.mosaic.all_parts).entries
        items = [self.icons.item(entry, WHITE, with_name=True) for entry in entries]
        heading = _text_block(
            "Teileübersicht", self.layout.font(HEADING_SIZE), self.layout.content_width
        )
        rows = _grid_rows(items, self.layout.content_width, WHITE)
        return [(heading, rows[0]), *((row,) for row in rows[1:])]

    def baseplate_step(self) -> Unit:
        """Schritt 1: alle Grundplatten nebeneinander auslegen."""
        area = Area(0, 0, self.mosaic.height, self.mosaic.width)
        render = render_iso(
            {},
            area,
            self.layout.view,
            baseplate_rgb=self.baseplate_rgb,
            max_width=self.layout.content_width,
            max_height=self.layout.step_render_height,
            supersample=1,
        )
        entries = PartsList.from_placements(self.mosaic.baseplates).entries
        return self.step_header(1, "Grundplatten auslegen"), self.callout(entries), render

    def section_intro(self, section: Section) -> Unit:
        """Abschnittsüberschrift mit Karte, wo der Abschnitt im Mosaik liegt."""
        text = f"Abschnitt {section.number} von {len(self.sections)}"
        return (
            _text_block(text, self.layout.font(HEADING_SIZE), self.layout.content_width),
            self.section_map(section, INTRO_MAP_PX),
        )

    def step_unit(self, number: int, step: BuildStep, built: Sequence[Placement]) -> Unit:
        """Kopf, Teile-Kasten und 3D-Ansicht eines Schritts."""
        section = step.section
        area = Area(section.row, section.col, section.rows, section.cols)
        cells = self.cells([*built, *step.placements], area, highlight=set(step.placements))
        render = render_iso(
            cells,
            area,
            self.layout.view,
            baseplate_rgb=self.baseplate_rgb,
            max_width=self.layout.content_width,
            max_height=self.layout.step_render_height,
            supersample=self.layout.supersample,
        )
        first_row = min(p.row for p in step.placements) + 1
        last_row = max(p.row + p.rows_spanned for p in step.placements)
        subtitle = f"Abschnitt {section.number} · Zeilen {first_row}–{last_row}"
        entries = PartsList.from_placements(step.placements).entries
        return self.step_header(number, subtitle), self.callout(entries), render

    def step_header(self, number: int, subtitle: str) -> Block:
        """Große Schrittnummer links, Untertitel daneben (wie in LEGO-Anleitungen)."""
        number_font, small = self.layout.font(STEP_SIZE), self.layout.font(BODY_SIZE)
        height = round(STEP_SIZE * LINE_SPACING)
        block = Image.new("RGB", (self.layout.content_width, height), WHITE)
        draw = ImageDraw.Draw(block)
        # anchor="lm": linke Kante, vertikal mittig am angegebenen Punkt ausrichten
        draw.text((0, height / 2), str(number), fill=BLACK, font=number_font, anchor="lm")
        left = round(number_font.getlength(str(number))) + BLOCK_SPACING
        draw.text((left, height / 2), subtitle, fill=GRAY_TEXT, font=small, anchor="lm")
        return block

    def callout(self, entries: Sequence[PartsListEntry]) -> Block:
        """Hellblauer Kasten mit den Teilen eines Schritts."""
        items = [self.icons.item(entry, CALLOUT_FILL, with_name=False) for entry in entries]
        inner_width = self.layout.content_width - 2 * CALLOUT_PADDING
        content = _stack(_grid_rows(items, inner_width, CALLOUT_FILL), ICON_GAP // 2, CALLOUT_FILL)
        size = (content.width + 2 * CALLOUT_PADDING, content.height + 2 * CALLOUT_PADDING)
        box = Image.new("RGB", size, WHITE)
        ImageDraw.Draw(box).rounded_rectangle(
            (0, 0, size[0] - 1, size[1] - 1),
            radius=CALLOUT_PADDING,
            fill=CALLOUT_FILL,
            outline=CALLOUT_BORDER,
            width=2,
        )
        box.paste(content, (CALLOUT_PADDING, CALLOUT_PADDING))
        return box

    def cells(
        self, placements: Iterable[Placement], area: Area, highlight: set[Placement] | None = None
    ) -> dict[tuple[int, int], RenderCell]:
        """Zellen innerhalb des Bereichs; Teile aus ``highlight`` werden hervorgehoben."""
        result = {}
        for placement in placements:
            if not _overlaps(placement, area):
                continue  # schneller Rechteck-Test statt jede Zelle einzeln zu prüfen
            content = RenderCell(
                rgb=placement.color.rgb,
                part_id=self.part_ids[placement],
                has_stud=placement.part.kind is not PartKind.TILE,
                highlighted=highlight is not None and placement in highlight,
            )
            for row, col in placement.cells():
                if area.contains(row, col):
                    result[(row, col)] = content
        return result

    def section_map(self, current: Section | None, max_px: int) -> Block:
        """Abschnittskarte; die Grundkarte wird pro Größe nur einmal gezeichnet (Cache)."""
        if max_px not in self._maps:
            self._maps[max_px] = _section_map(self.mosaic, self.sections, self.layout, max_px)
        base, stud_px = self._maps[max_px]
        if current is None:
            return base
        marked = base.copy()  # Kopie, damit die gecachte Grundkarte unverändert bleibt
        box = (
            current.col * stud_px,
            current.row * stud_px,
            (current.col + current.cols) * stud_px - 1,
            (current.row + current.rows) * stud_px - 1,
        )
        ImageDraw.Draw(marked).rectangle(box, outline=MAP_HIGHLIGHT, width=MAP_HIGHLIGHT_WIDTH)
        return marked


class _IconCache:
    """Rendert jedes Teil pro Farbe und Hintergrund nur einmal (Memoization)."""

    def __init__(self, layout: PageLayout) -> None:
        self.layout = layout
        self._icons: dict[tuple[PartType, LegoColor, RgbTuple], Block] = {}

    def icon(self, part: PartType, color: LegoColor, background: RgbTuple) -> Block:
        key = (part, color, background)
        if key not in self._icons:
            self._icons[key] = render_part(part, color, self.layout, background)
        return self._icons[key]

    def item(self, entry: PartsListEntry, background: RgbTuple, *, with_name: bool) -> Block:
        """Teilbild mit „n×“ darunter, optional zusätzlich der Farbname."""
        icon = self.icon(entry.part, entry.color, background)
        lines = [(f"{entry.quantity}×", self.layout.font(BODY_SIZE), BLACK)]
        if with_name:
            lines.append((entry.color.name, self.layout.font(SMALL_SIZE), GRAY_TEXT))
        width = max(icon.width, *(round(font.getlength(text)) for text, font, _ in lines))
        line_height = round(BODY_SIZE * LINE_SPACING)
        block = Image.new("RGB", (width, icon.height + line_height * len(lines)), background)
        block.paste(icon, ((width - icon.width) // 2, 0))
        draw = ImageDraw.Draw(block)
        for index, (text, font, rgb) in enumerate(lines):
            # anchor="ma": horizontal mittig, oben am Punkt ausrichten
            draw.text(
                (width / 2, icon.height + index * line_height),
                text,
                fill=rgb,
                font=font,
                anchor="ma",
            )
        return block


def render_part(
    part: PartType, color: LegoColor, layout: PageLayout, background: RgbTuple
) -> Block:
    """Ein einzelnes Teil als 3D-Bild, lange Seite waagerecht."""
    area = Area(0, 0, part.width_studs, part.length_studs)
    if part.kind is PartKind.BASEPLATE:
        return render_iso(
            {},
            area,
            layout.view,
            baseplate_rgb=color.rgb,
            max_width=ICON_MAX_PX,
            max_height=ICON_MAX_PX,
            supersample=layout.supersample,
            background=background,
        )
    content = RenderCell(color.rgb, part_id=0, has_stud=part.kind is not PartKind.TILE)
    cells = {(row, col): content for row in range(area.rows) for col in range(area.cols)}
    return render_iso(
        cells,
        area,
        layout.view,
        baseplate_rgb=None,
        max_width=ICON_MAX_PX,
        max_height=ICON_MAX_PX,
        supersample=layout.supersample,
        scale=ICON_SCALE,
        background=background,
    )


# --- Blöcke und Seiten -----------------------------------------------------------------


def _section_map(
    mosaic: Mosaic, sections: Sequence[Section], layout: PageLayout, max_px: int
) -> tuple[Block, int]:
    """Kleine Draufsicht mit Abschnittsgrenzen und -nummern; liefert Bild und Noppengröße."""
    stud_px = max(1, min(layout.max_stud_px, max_px // max(mosaic.width, mosaic.height)))
    image = render_placements(
        mosaic.placements,
        region=Region(0, 0, mosaic.height, mosaic.width),
        stud_px=stud_px,
        draw_part_edges=False,
    )
    draw = ImageDraw.Draw(image)
    font = layout.font(max(SMALL_SIZE, layout.section_studs * stud_px // 3))
    for section in sections:
        box = (
            section.col * stud_px,
            section.row * stud_px,
            (section.col + section.cols) * stud_px - 1,
            (section.row + section.rows) * stud_px - 1,
        )
        draw.rectangle(box, outline=WHITE, width=1)
        center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        # stroke_*: weiße Schrift mit schwarzem Rand ist auf jeder Farbe lesbar
        draw.text(
            center,
            str(section.number),
            fill=WHITE,
            font=font,
            anchor="mm",
            stroke_width=2,
            stroke_fill=BLACK,
        )
    return image, stud_px


def _overlaps(placement: Placement, area: Area) -> bool:
    """True, wenn sich das Rechteck des Teils und der Bereich überschneiden."""
    return (
        placement.row < area.row + area.rows
        and area.row < placement.row + placement.rows_spanned
        and placement.col < area.col + area.cols
        and area.col < placement.col + placement.cols_spanned
    )


def _grid_rows(items: Sequence[Block], width: int, background: RgbTuple) -> list[Block]:
    """Ordnet Blöcke zeilenweise nebeneinander an (Umbruch, wenn die Breite voll ist)."""
    rows: list[list[Block]] = [[]]
    used = 0
    for item in items:
        needed = item.width + (ICON_GAP if rows[-1] else 0)
        if rows[-1] and used + needed > width:
            rows.append([])
            used, needed = 0, item.width
        rows[-1].append(item)
        used += needed
    return [_side_by_side(row, background) for row in rows if row]


def _side_by_side(items: Sequence[Block], background: RgbTuple) -> Block:
    """Blöcke nebeneinander, unten bündig."""
    width = sum(item.width for item in items) + ICON_GAP * (len(items) - 1)
    height = max(item.height for item in items)
    row = Image.new("RGB", (width, height), background)
    left = 0
    for item in items:
        row.paste(item, (left, height - item.height))
        left += item.width + ICON_GAP
    return row


def _stack(blocks: Sequence[Block], spacing: int, background: RgbTuple) -> Block:
    """Blöcke untereinander, links bündig."""
    width = max(block.width for block in blocks)
    height = sum(block.height for block in blocks) + spacing * (len(blocks) - 1)
    stacked = Image.new("RGB", (width, height), background)
    top = 0
    for block in blocks:
        stacked.paste(block, (0, top))
        top += block.height + spacing
    return stacked


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


def paginate(document: Sequence[Unit | PageBreak], layout: PageLayout) -> list[Image.Image]:
    """Verteilt die Einheiten auf Seiten; eine Einheit wird nie getrennt.

    Ist eine einzelne Einheit höher als eine Seite, wird diese Seite entsprechend höher.
    """
    pages: list[list[Block]] = []
    current: list[Block] = []
    used = 0
    for item in document:
        if isinstance(item, PageBreak):
            if current:
                pages.append(current)
            current, used = [], 0
            continue
        height = sum(block.height for block in item) + BLOCK_SPACING * (len(item) - 1)
        needed = height + (BLOCK_SPACING if current else 0)
        if current and used + needed > layout.content_height:
            pages.append(current)
            current, used, needed = [], 0, height
        current += item
        used += needed
    if current:
        pages.append(current)
    return [_compose(blocks, number, len(pages), layout) for number, blocks in enumerate(pages, 1)]


def _compose(blocks: Sequence[Block], number: int, total: int, layout: PageLayout) -> Image.Image:
    """Setzt die Blöcke untereinander auf eine Seite und schreibt die Seitenzahl darunter."""
    content_height = sum(block.height for block in blocks) + BLOCK_SPACING * (len(blocks) - 1)
    height = max(layout.height, content_height + 2 * layout.margin)
    page = Image.new("RGB", (layout.width, height), WHITE)
    top = layout.margin
    for block in blocks:
        page.paste(block, (layout.margin, top))
        top += block.height + BLOCK_SPACING
    ImageDraw.Draw(page).text(
        (layout.width / 2, height - layout.margin / 2),
        f"{number} / {total}",
        fill=GRAY_TEXT,
        font=layout.font(SMALL_SIZE),
        anchor="mm",
    )
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
