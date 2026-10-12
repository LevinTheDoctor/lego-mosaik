"""Bauanleitung im LEGO-Stil als PNG-Seiten und mehrseitiges PDF (nur mit Pillow).

Aufbau (wie bei LEGO Art: erst die stabile Grundlage, dann das Bild):

1. Titelseite: gerendertes Mosaik (3D), Abschnittskarte und Kennzahlen
2. Teileübersicht: alle Teile als gerenderte Einzelteile mit Stückzahl
3. Unterbau auslegen: Grundplatten bzw. LEGO-Art-Paneele
4. Paneele verbinden: Verbindungsplan mit den Technic-Pins
5. Rahmen: erst die Stützlagen (auf Lücke über den Nähten), dann der sichtbare Rahmen
6. Pro Abschnitt eine Fortschrittskarte (was steht schon, wo geht es weiter), dann
   die Bauschritte: Teile-Kasten („3×“ unter jedem Teilbild) und 3D-Ansicht; neue
   Teile sind orange umrandet
7. Fertig: das ganze Mosaik

Die Schritte sind exakt die der LDraw-Datei (``domain.sections.assembly_steps``).
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
from lego_mosaik.domain.sections import (
    BuildStep,
    Section,
    StepKind,
    assembly_steps,
    split_sections,
)
from lego_mosaik.outputs.iso_render import Area, IsoView, RenderCell, Underlay, render_iso
from lego_mosaik.outputs.plans import (
    connection_plan,
    map_stud_px,
    pin_icon,
    section_map,
)
from lego_mosaik.outputs.rendering import Font

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
# Noch nicht gebaute Flächen auf den Karten: neutral, damit jede Steinfarbe auffällt
# (in Unterbau-Farbe wäre z. B. ein schwarzer Rahmen auf schwarzen Paneelen unsichtbar)
UNBUILT_RGB: RgbTuple = (215, 215, 215)
TITLE_SIZE, STEP_SIZE, HEADING_SIZE, BODY_SIZE, SMALL_SIZE = 44, 56, 30, 22, 18
BLOCK_SPACING = 18
LINE_SPACING = 1.35
ICON_SCALE = 1.4  # Pixel pro LDU für Teilbilder (alle Teile im selben Maßstab)
ICON_MAX_PX = 220  # große Teile (Grundplatten) werden auf diese Größe eingepasst
PIN_ICON_PX = 70  # Breite des Pin-Symbols
ICON_GAP = 22
CALLOUT_PADDING = 16
COVER_MAP_PX = 420  # Abschnittskarte auf der Titelseite
INTRO_MAP_PX = 300  # Fortschrittskarte am Anfang jedes Abschnitts
PLAN_PX = 700  # Verbindungsplan der Paneele
COVER_RENDER_HEIGHT = 900
LARGE_RENDER_CELLS = 4096  # ab so vielen Zellen ohne Supersampling (schneller)

type Block = Image.Image
type Unit = tuple[Block, ...]  # Blöcke, die auf derselben Seite bleiben

# Überschriften der Schritte vor dem Bild
_SUBSTRATE_TITLES = {
    PartKind.BASEPLATE: "Grundplatten auslegen",
    PartKind.PANEL: "LEGO-Art-Paneele auslegen",
}
_LOOSE_BASEPLATES_HINT = (
    "Hinweis: Grundplatten lassen sich nicht miteinander verbinden. Nur Rahmen und "
    "Teile über den Nähten halten sie zusammen; stabiler wird das Bild mit "
    "LEGO-Art-Paneelen („--unterbau art“)."
)
_CONNECTOR_HINT = (
    "Je einen Pin zur Hälfte in das Loch an der Seite eines Paneels stecken und das "
    "Nachbarpaneel aufschieben. Orange im Plan: Lage der Pins an jeder Naht."
)
_SUPPORT_HINT = (
    "Die Plates liegen auf Lücke: Sie überbrücken die Nähte zwischen den Platten "
    "und heben den Rahmen über das Bild."
)


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
        self.steps = assembly_steps(
            mosaic, section_studs=layout.section_studs, rows_per_step=layout.rows_per_step
        )
        # Jede sichtbare oder stützende Platzierung bekommt eine Nummer; daran
        # erkennt der Renderer Teilgrenzen (Unterbau und Pins rendert er anders)
        stacked = mosaic.supports + mosaic.surface
        self.part_ids = {placement: index for index, placement in enumerate(stacked)}
        self.icons = _IconCache(layout)
        self.underlay = _underlay(mosaic.substrate)
        self.full_area = Area(0, 0, mosaic.height, mosaic.width)

    def document(self) -> list[Unit | PageBreak]:
        """Alle Einheiten; Unterbau, Verbinder, Rahmen und jeder Abschnitt auf neuer Seite."""
        doc: list[Unit | PageBreak] = [self.cover(), PAGE_BREAK, *self.inventory(), PAGE_BREAK]
        built: list[Placement] = []  # was schon steht (für 3D-Ansichten und Karten)
        previous_section = 0
        for number, step in enumerate(self.steps, start=1):
            if step.kind is StepKind.SECTION and step.section is not None:
                if step.section.number != previous_section:
                    doc += [PAGE_BREAK, self.section_intro(step.section, built)]
                    previous_section = step.section.number
                doc.append(self.section_step(number, step, built))
            else:
                doc += [self.base_step(number, step, built), PAGE_BREAK]
            if step.kind not in (StepKind.SUBSTRATE, StepKind.CONNECTORS):
                built += step.placements
        return [*doc, PAGE_BREAK, self.finished()]

    def cover(self) -> Unit:
        """Titel, 3D-Bild des fertigen Mosaiks, Kennzahlen und Abschnittskarte."""
        mosaic, layout = self.mosaic, self.layout
        parts = PartsList.from_placements(mosaic.all_parts).total_quantity
        facts = (
            f"{mosaic.width} × {mosaic.height} Noppen · {len(self.sections)} Abschnitte · "
            f"{len(self.steps)} Schritte · {parts} Teile · {len(mosaic.used_colors())} Farben"
        )
        return (
            _text_block(mosaic.title, layout.font(TITLE_SIZE), layout.content_width),
            self.finished_render,
            _text_block(facts, layout.font(BODY_SIZE), layout.content_width),
            self.section_map(mosaic.surface, None, COVER_MAP_PX),
        )

    @functools.cached_property
    def finished_render(self) -> Block:
        """3D-Bild des fertigen Mosaiks.

        ``cached_property`` rechnet beim ersten Zugriff und merkt sich das Ergebnis;
        Titel- und Schlussseite teilen sich so ein (teures) Rendering.
        """
        large = self.mosaic.width * self.mosaic.height > LARGE_RENDER_CELLS
        return render_iso(
            self.cells(self.mosaic.supports + self.mosaic.surface, self.full_area),
            self.full_area,
            self.layout.view,
            underlay=self.underlay,
            max_width=self.layout.content_width,
            max_height=COVER_RENDER_HEIGHT,
            supersample=1 if large else self.layout.supersample,
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

    def base_step(self, number: int, step: BuildStep, built: Sequence[Placement]) -> Unit:
        """Ein Schritt vor dem Bild: Unterbau, Verbinder, Rahmen-Unterbau oder Rahmen."""
        entries = PartsList.from_placements(step.placements).entries
        head = (self.step_header(number, _base_title(step)), self.callout(entries))
        match step.kind:
            case StepKind.SUBSTRATE:
                return (*head, *self.substrate_view(step.placements))
            case StepKind.CONNECTORS:
                return (*head, self.hint(_CONNECTOR_HINT), self.connection_plan())
            case StepKind.FRAME_SUPPORT:
                return (*head, self.hint(_SUPPORT_HINT), self.frame_view(step, built))
            case _:
                return (*head, self.frame_view(step, built))

    def substrate_view(self, plates: Sequence[Placement]) -> tuple[Block, ...]:
        """3D-Ansicht des leeren Unterbaus; lose Grundplatten bekommen einen Hinweis."""
        render = render_iso(
            {},
            _covered_area(plates),
            self.layout.view,
            underlay=self.underlay,
            max_width=self.layout.content_width,
            max_height=self.layout.step_render_height,
            supersample=1,
        )
        loose = len(plates) > 1 and plates[0].part.kind is PartKind.BASEPLATE
        return (self.hint(_LOOSE_BASEPLATES_HINT), render) if loose else (render,)

    def connection_plan(self) -> Block:
        """Schema mit nummerierten Paneelen und den Pins an den Nähten."""
        plates = self.mosaic.substrate
        area = _covered_area(plates)
        stud_px = map_stud_px(area.cols, area.rows, PLAN_PX, self.layout.max_stud_px)
        font = self.layout.font(max(SMALL_SIZE, self.layout.section_studs * stud_px // 3))
        return connection_plan(plates, self.mosaic.connectors, stud_px=stud_px, font=font)

    def frame_view(self, step: BuildStep, built: Sequence[Placement]) -> Block:
        """3D-Ansicht des ganzen Mosaiks mit den neuen Rahmenteilen hervorgehoben."""
        cells = self.cells([*built, *step.placements], self.full_area, set(step.placements))
        large = self.mosaic.width * self.mosaic.height > LARGE_RENDER_CELLS
        return render_iso(
            cells,
            self.full_area,
            self.layout.view,
            underlay=self.underlay,
            max_width=self.layout.content_width,
            max_height=self.layout.step_render_height,
            supersample=1 if large else self.layout.supersample,
        )

    def section_intro(self, section: Section, built: Sequence[Placement]) -> Unit:
        """Abschnittsüberschrift mit Fortschrittskarte: was steht schon, wo geht es weiter."""
        text = f"Abschnitt {section.number} von {len(self.sections)}"
        caption = "Rot umrandet: hier geht es weiter. Farbig: schon gebaut. Grau: kommt noch."
        return (
            _text_block(text, self.layout.font(HEADING_SIZE), self.layout.content_width),
            self.section_map(built, section, INTRO_MAP_PX),
            self.hint(caption),
        )

    def section_step(self, number: int, step: BuildStep, built: Sequence[Placement]) -> Unit:
        """Kopf, Teile-Kasten und 3D-Ansicht eines Bildschritts."""
        assert step.section is not None  # nur für Bildschritte aufgerufen
        section = step.section
        area = Area(section.row, section.col, section.rows, section.cols)
        cells = self.cells([*built, *step.placements], area, highlight=set(step.placements))
        render = render_iso(
            cells,
            area,
            self.layout.view,
            underlay=self.underlay,
            max_width=self.layout.content_width,
            max_height=self.layout.step_render_height,
            supersample=self.layout.supersample,
        )
        first_row = min(p.row for p in step.placements) + 1
        last_row = max(p.row + p.rows_spanned for p in step.placements)
        subtitle = f"Abschnitt {section.number} · Zeilen {first_row}–{last_row}"
        entries = PartsList.from_placements(step.placements).entries
        return self.step_header(number, subtitle), self.callout(entries), render

    def finished(self) -> Unit:
        """Schlussseite: das ganze Mosaik und was es zusammenhält."""
        mosaic = self.mosaic
        text = f"Fertig! Alle {len(self.sections)} Abschnitte sind gebaut."
        if mosaic.connectors:
            text += (
                f" Die {len(mosaic.substrate)} Paneele halten mit "
                f"{len(mosaic.connectors)} Technic-Pins zusammen."
            )
        if mosaic.supports:
            text += " Der erhöhte Rahmen überbrückt zusätzlich alle Nähte am Rand."
        return (
            _text_block(text, self.layout.font(HEADING_SIZE), self.layout.content_width),
            self.finished_render,
        )

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

    def hint(self, text: str) -> Block:
        """Erklärender Text in normaler Schriftgröße."""
        return _text_block(text, self.layout.font(BODY_SIZE), self.layout.content_width)

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
        """Zellen innerhalb des Bereichs; Teile aus ``highlight`` werden hervorgehoben.

        Spätere Teile überschreiben frühere in derselben Zelle (der Rahmen liegt auf
        seinen Stützteilen und ist höher, also gewinnt er).
        """
        result = {}
        for placement in placements:
            if not _overlaps(placement, area):
                continue  # schneller Rechteck-Test statt jede Zelle einzeln zu prüfen
            content = RenderCell(
                rgb=placement.color.rgb,
                part_id=self.part_ids[placement],
                has_stud=placement.part.kind is not PartKind.TILE,
                highlighted=highlight is not None and placement in highlight,
                top_ldu=placement.top_ldu,
            )
            for row, col in placement.cells():
                if area.contains(row, col):
                    result[(row, col)] = content
        return result

    def section_map(
        self, placements: Iterable[Placement], current: Section | None, max_px: int
    ) -> Block:
        """Draufsicht mit Abschnittsnummern; ``current`` wird rot umrandet."""
        mosaic = self.mosaic
        stud_px = map_stud_px(mosaic.width, mosaic.height, max_px, self.layout.max_stud_px)
        return section_map(
            placements,
            self.sections,
            (mosaic.width, mosaic.height),
            stud_px=stud_px,
            font=self.layout.font(max(SMALL_SIZE, self.layout.section_studs * stud_px // 3)),
            background=UNBUILT_RGB,
            current=current,
        )


def _base_title(step: BuildStep) -> str:
    """Untertitel der Schritte vor dem Bild."""
    match step.kind:
        case StepKind.SUBSTRATE:
            return _SUBSTRATE_TITLES.get(step.placements[0].part.kind, "Unterbau auslegen")
        case StepKind.CONNECTORS:
            return "Paneele mit Technic-Pins verbinden"
        case StepKind.FRAME_SUPPORT:
            return f"Rahmen-Unterbau, Lage {step.level + 1}"
        case _:
            return "Rahmen"


def _underlay(substrate: Sequence[Placement]) -> Underlay | None:
    """Darstellung des Unterbaus: Farbe, Dicke und Fugen zwischen mehreren Platten."""
    if not substrate:
        return None
    part = substrate[0].part
    seam = part.length_studs if len(substrate) > 1 else None
    return Underlay(substrate[0].color.rgb, thickness_ldu=part.height_ldu, seam_studs=seam)


def _covered_area(plates: Sequence[Placement]) -> Area:
    """Bereich, den die Unterbau-Platten zusammen abdecken (kann größer als das Bild sein)."""
    rows = max(plate.row + plate.rows_spanned for plate in plates)
    cols = max(plate.col + plate.cols_spanned for plate in plates)
    return Area(0, 0, rows, cols)


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
    """Ein einzelnes Teil als Bild, lange Seite waagerecht."""
    area = Area(0, 0, part.width_studs, part.length_studs)
    if part.kind is PartKind.CONNECTOR:
        return pin_icon(color.rgb, background, PIN_ICON_PX)
    if part.kind in (PartKind.BASEPLATE, PartKind.PANEL):
        return render_iso(
            {},
            area,
            layout.view,
            underlay=Underlay(color.rgb, thickness_ldu=part.height_ldu),
            max_width=ICON_MAX_PX,
            max_height=ICON_MAX_PX,
            supersample=layout.supersample,
            background=background,
        )
    content = RenderCell(
        color.rgb,
        part_id=0,
        has_stud=part.kind is not PartKind.TILE,
        top_ldu=part.height_ldu,
    )
    cells = {(row, col): content for row in range(area.rows) for col in range(area.cols)}
    return render_iso(
        cells,
        area,
        layout.view,
        underlay=None,
        max_width=ICON_MAX_PX,
        max_height=ICON_MAX_PX,
        supersample=layout.supersample,
        scale=ICON_SCALE,
        background=background,
    )


# --- Blöcke und Seiten -----------------------------------------------------------------


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
