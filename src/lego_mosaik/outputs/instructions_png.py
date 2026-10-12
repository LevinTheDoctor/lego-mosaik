"""Bauanleitung im Stil eines LEGO-Hefts als PNG-Seiten und mehrseitiges PDF (nur Pillow).

Aufbau wie ein Anleitungsheft (Querformat):

1. Titelblatt: dunkler Hintergrund, Name des Mosaiks, großes 3D-Bild, Kennzahlen
2. Übersicht: Abschnittskarte und Bauablauf
3. Bauschritte, je Schritt eine Seite: große Schrittnummer, Teile-Kasten („3x“ unter
   jedem Teilbild) und 3D-Ansicht mit den neuen Teilen orange umrandet. Erst die
   Grundlage (Unterbau, Pins bzw. Verbindungslage, Rahmen), dann das Bild Abschnitt für
   Abschnitt; der erste Schritt eines Abschnitts zeigt oben rechts die Fortschrittskarte
4. Fertig: das ganze Mosaik
5. Teileliste wie hinten in LEGO-Heften: jedes Teil mit Stückzahl, Farbe und Element-ID

Die Schritte sind exakt die der LDraw-Datei (``domain.sections.assembly_steps``). Eine
Einheit (z. B. ein Schritt) wird nie über zwei Seiten getrennt.
"""

import functools
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
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
from lego_mosaik.domain.shapes import box, cell_outline, stud_visible
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
CM_PER_STUD = 0.8  # eine Noppe ist 8 mm breit

WHITE: RgbTuple = (255, 255, 255)
BLACK: RgbTuple = (0, 0, 0)
GRAY_TEXT: RgbTuple = (90, 90, 90)
LIGHT_TEXT: RgbTuple = (200, 205, 215)
CALLOUT_FILL: RgbTuple = (219, 234, 246)  # hellblauer Teile-Kasten wie in LEGO-Heften
CALLOUT_BORDER: RgbTuple = (120, 160, 200)
COVER_BACKGROUND: RgbTuple = (20, 26, 36)  # dunkel wie die Verpackungen von LEGO Art
COVER_ACCENT: RgbTuple = (255, 205, 0)  # gelbes Band und Etikett
PAGE_NUMBER_FILL: RgbTuple = (235, 240, 246)
# Noch nicht gebaute Flächen auf den Karten: neutral, damit jede Steinfarbe auffällt
# (in Unterbau-Farbe wäre z. B. ein schwarzer Rahmen auf schwarzen Paneelen unsichtbar)
UNBUILT_RGB: RgbTuple = (215, 215, 215)

COVER_TITLE_SIZE, COVER_INFO_SIZE, BADGE_SIZE = 88, 34, 26
STEP_SIZE, HEADING_SIZE, BODY_SIZE, SMALL_SIZE, QUANTITY_SIZE = 110, 40, 24, 19, 26
PAGE_NUMBER_SIZE = 30
BLOCK_SPACING = 22
LINE_SPACING = 1.3
COVER_TEXT_SHARE = 0.36  # Anteil der Titelblatt-Breite für den Text links
COVER_ACCENT_PX = 18
ICON_SCALE = 1.4  # Pixel pro LDU für Teilbilder (alle Teile im selben Maßstab)
ICON_MAX_PX = 220  # große Teile (Grundplatten) werden auf diese Größe eingepasst
LARGE_PART_STUDS = 8  # längere Teile (z. B. Plate 16 × 16) als Icon einpassen
PIN_ICON_PX = 70  # Breite des Pin-Symbols
ICON_GAP = 22
CALLOUT_PADDING = 16
CALLOUT_SHARE = 0.62  # höchstens dieser Anteil der Breite für den Teile-Kasten
PROGRESS_MAP_PX = 260  # Fortschrittskarte oben rechts im ersten Schritt eines Abschnitts
OVERVIEW_MAP_PX = 760  # Abschnittskarte auf der Übersichtsseite
PLAN_PX = 700  # Verbindungsplan der Paneele
LARGE_RENDER_CELLS = 4096  # ab so vielen Zellen ohne Supersampling (schneller)

type Block = Image.Image
type Unit = tuple[Block, ...]  # Blöcke, die auf derselben Seite bleiben
type _View = Callable[[int, int], Block]  # zeichnet eine Ansicht in höchstens Breite × Höhe

# Überschriften der Schritte vor dem Bild
_SUBSTRATE_TITLES = {
    PartKind.BASEPLATE: "Grundplatten auslegen",
    PartKind.PANEL: "LEGO-Art-Paneele auslegen",
    PartKind.PLATE: "Große Platten auslegen",
}
_LOOSE_BASEPLATES_HINT = (
    "Grundplatten lassen sich nicht miteinander verbinden. Nur Rahmen und Teile über den "
    "Nähten halten sie zusammen; stabiler wird das Bild mit „--unterbau art“ oder „platten“."
)
_CONNECTOR_HINT = (
    "Je einen Pin zur Hälfte in das Loch an der Seite eines Paneels stecken und das "
    "Nachbarpaneel aufschieben. Orange im Plan: Lage der Pins an jeder Naht."
)
_BOND_HINT = (
    "Die Plates liegen auf Lücke: Jede Naht zwischen den großen Platten liegt mitten "
    "unter Plates. So hält das ganze Bild fest zusammen."
)
_SUPPORT_HINT = (
    "Die Plates liegen auf Lücke: Sie überbrücken die Nähte zwischen den Platten "
    "und heben den Rahmen über das Bild."
)
_RELIEF_HINT = "Diese Plates heben die Bildteile darüber an (Relief)."
_SHAPES_HINT = "Runde und schräge Teile: auf die Drehung achten, sie ist in der Ansicht zu sehen."


class PageBreak:
    """Markiert einen Seitenumbruch im Dokument."""


PAGE_BREAK = PageBreak()


@dataclass(frozen=True, slots=True)
class FullPage:
    """Eine fertig gestaltete Seite ohne Rand und Seitenzahl (z. B. das Titelblatt)."""

    image: Image.Image


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
    step_render_height: int = 900
    font_paths: tuple[Path, ...] = ()
    bold_font_paths: tuple[Path, ...] = ()

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin

    @property
    def content_height(self) -> int:
        # unten bleibt Platz für die Seitenzahl
        return self.height - 2 * self.margin - PAGE_NUMBER_SIZE

    def font(self, size: int) -> Font:
        """Erste vorhandene TrueType-Schrift; sonst Pillows Standardschrift (ohne Umlaute)."""
        return _load_font(self.font_paths, size)

    def bold(self, size: int) -> Font:
        """Fette Schrift für Schrittnummern und Titel; ohne fette Schrift die normale."""
        return _load_font(self.bold_font_paths + self.font_paths, size)


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
            title=mosaic.title,
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
        self.part_ids = {placement: index for index, placement in enumerate(mosaic.stacked)}
        self.icons = _IconCache(layout)
        self.underlay = _underlay(mosaic.substrate)
        self.full_area = Area(0, 0, mosaic.height, mosaic.width)
        self.parts = PartsList.from_placements(mosaic.all_parts)

    def document(self) -> list[Unit | PageBreak | FullPage]:
        """Titelblatt, Übersicht, je Schritt eine Seite, Schlussseite und Teileliste."""
        doc: list[Unit | PageBreak | FullPage] = [self.cover(), self.overview(), PAGE_BREAK]
        built: list[Placement] = []  # was schon steht (für 3D-Ansichten und Karten)
        previous_section = 0
        for number, step in enumerate(self.steps, start=1):
            if step.section is not None:  # Bild- und Reliefschritte gehören zum Abschnitt
                first = step.section.number != previous_section
                previous_section = step.section.number
                progress = self.progress_map(step.section, built) if first else None
                doc += [self.section_step(number, step, built, progress), PAGE_BREAK]
            else:
                doc += [self.base_step(number, step, built), PAGE_BREAK]
            if step.kind not in (StepKind.SUBSTRATE, StepKind.CONNECTORS):
                built += step.placements
        return [*doc, self.finished(), PAGE_BREAK, *self.inventory()]

    # --- Titelblatt und Übersicht -------------------------------------------------------

    def cover(self) -> FullPage:
        """Titelblatt: Name, Kennzahlen und großes 3D-Bild auf dunklem Grund."""
        layout, mosaic = self.layout, self.mosaic
        page = Image.new("RGB", (layout.width, layout.height), COVER_BACKGROUND)
        draw = ImageDraw.Draw(page)
        draw.rectangle((0, 0, layout.width, COVER_ACCENT_PX), fill=COVER_ACCENT)
        text_width = round(layout.width * COVER_TEXT_SHARE) - layout.margin
        top = layout.margin + COVER_ACCENT_PX
        top += _badge(page, (layout.margin, top), "MOSAIK", layout.bold(BADGE_SIZE))
        title_font = _fitting_font(layout, mosaic.title, text_width, COVER_TITLE_SIZE)
        for line in _wrap(mosaic.title, title_font, text_width):
            draw.text((layout.margin, top + BLOCK_SPACING), line, fill=WHITE, font=title_font)
            top += round(_font_size(title_font) * LINE_SPACING)
        info = [
            f"{self.parts.total_quantity} Teile",
            f"{mosaic.width} × {mosaic.height} Noppen",
            f"ca. {mosaic.width * CM_PER_STUD:.0f} × {mosaic.height * CM_PER_STUD:.0f} cm",
            f"{len(mosaic.used_colors())} Farben",
        ]
        info_font = layout.font(COVER_INFO_SIZE)
        line_height = round(COVER_INFO_SIZE * LINE_SPACING)
        bottom = layout.height - layout.margin - line_height * (len(info) + 1)
        for index, line in enumerate(info):
            draw.text((layout.margin, bottom + index * line_height), line, LIGHT_TEXT, info_font)
        draw.text(
            (layout.margin, bottom + len(info) * line_height),
            "Bauanleitung",
            COVER_ACCENT,
            layout.bold(COVER_INFO_SIZE),
        )
        render_left = layout.margin + text_width + layout.margin
        render = self.render_all(
            max_width=layout.width - render_left - layout.margin,
            max_height=layout.height - 2 * layout.margin - COVER_ACCENT_PX,
            background=COVER_BACKGROUND,
        )
        page.paste(
            render,
            (
                render_left + (layout.width - render_left - layout.margin - render.width) // 2,
                (layout.height + COVER_ACCENT_PX - render.height) // 2,
            ),
        )
        return FullPage(page)

    def overview(self) -> Unit:
        """Übersicht: Abschnittskarte und Ablauf (Grundlage, dann Abschnitt für Abschnitt)."""
        layout = self.layout
        heading = _text_block("So wird gebaut", layout.bold(HEADING_SIZE), layout.content_width)
        map_px = min(OVERVIEW_MAP_PX, layout.content_width // 2, layout.content_height // 2)
        text_width = layout.content_width - map_px - ICON_GAP
        lines = [f"{index}. {text}" for index, text in enumerate(self.build_order(), start=1)]
        body = _stack(
            [_text_block(line, layout.font(BODY_SIZE), text_width) for line in lines],
            BLOCK_SPACING // 2,
            WHITE,
        )
        plan = self.section_map(self.mosaic.stacked, None, map_px)
        return heading, _side_by_side([body, plan], WHITE, align_top=True)

    def build_order(self) -> list[str]:
        """Die Bauphasen in Worten, passend zu den Schritten."""
        kinds = {step.kind for step in self.steps}
        order = []
        if StepKind.SUBSTRATE in kinds:
            plates = self.mosaic.substrate
            order.append(f"Unterbau auslegen ({len(plates)} × {plates[0].part.name})")
        if StepKind.CONNECTORS in kinds:
            order.append(f"Paneele mit {len(self.mosaic.connectors)} Technic-Pins verbinden")
        if StepKind.BOND in kinds:
            order.append("Platten mit einer Lage Plates auf Lücke verbinden")
        if kinds & {StepKind.FRAME_SUPPORT, StepKind.FRAME}:
            order.append("Rahmen bauen")
        order.append(
            f"Das Bild in {len(self.sections)} Abschnitten bauen, Reihenfolge wie auf der "
            "Karte. Jeder Abschnitt beginnt oben rechts mit einer Karte, wo es weitergeht."
        )
        return order

    @functools.cached_property
    def finished_render(self) -> Block:
        """3D-Bild des fertigen Mosaiks für die Schlussseite (einmal gerechnet)."""
        return self.render_all(
            max_width=self.layout.content_width,
            max_height=self.layout.content_height - 2 * HEADING_SIZE - BLOCK_SPACING,
        )

    def render_all(self, *, max_width: int, max_height: int, background: RgbTuple = WHITE) -> Block:
        """3D-Bild des ganzen fertigen Mosaiks."""
        large = self.mosaic.width * self.mosaic.height > LARGE_RENDER_CELLS
        return render_iso(
            self.cells(self.mosaic.stacked, self.full_area),
            self.full_area,
            self.layout.view,
            underlay=self.underlay,
            max_width=max_width,
            max_height=max_height,
            supersample=1 if large else self.layout.supersample,
            background=background,
        )

    # --- Schritte ------------------------------------------------------------------------

    def base_step(self, number: int, step: BuildStep, built: Sequence[Placement]) -> Unit:
        """Ein Schritt vor dem Bild: Unterbau, Verbinder, Verbindungslage oder Rahmen."""
        hint: str | None = None
        match step.kind:
            case StepKind.SUBSTRATE:
                loose = len(step.placements) > 1
                if loose and step.placements[0].part.kind is PartKind.BASEPLATE:
                    hint = _LOOSE_BASEPLATES_HINT
                return self.step_page(number, step, _base_title(step), hint, self.substrate_view)
            case StepKind.CONNECTORS:
                return self.step_page(
                    number, step, _base_title(step), _CONNECTOR_HINT, self.connection_plan
                )
            case StepKind.BOND:
                hint = _BOND_HINT
            case StepKind.FRAME_SUPPORT:
                hint = _SUPPORT_HINT

        def view(width: int, height: int) -> Block:
            return self.full_view(step, built, width, height)

        return self.step_page(number, step, _base_title(step), hint, view)

    def section_step(
        self,
        number: int,
        step: BuildStep,
        built: Sequence[Placement],
        progress: Block | None,
    ) -> Unit:
        """Ein Bildschritt: Kopf mit Teilen (und ggf. Fortschrittskarte), dann 3D-Ansicht."""
        assert step.section is not None  # nur für Bild- und Reliefschritte aufgerufen
        section = step.section
        area = Area(section.row, section.col, section.rows, section.cols)
        cells = self.cells([*built, *step.placements], area, highlight=set(step.placements))
        hint = None
        if step.kind is StepKind.RELIEF:
            hint = _RELIEF_HINT
        elif any(placement.part.is_shaped for placement in step.placements):
            hint = _SHAPES_HINT

        def view(width: int, height: int) -> Block:
            return render_iso(
                cells,
                area,
                self.layout.view,
                underlay=self.underlay,
                max_width=width,
                max_height=min(height, self.layout.step_render_height),
                supersample=self.layout.supersample,
            )

        subtitle = _section_subtitle(step, section, len(self.sections))
        return self.step_page(number, step, subtitle, hint, view, progress)

    def step_page(
        self,
        number: int,
        step: BuildStep,
        subtitle: str,
        hint: str | None,
        view: _View,
        progress: Block | None = None,
    ) -> Unit:
        """Seite eines Schritts wie im LEGO-Heft.

        Oben links die große Schrittnummer mit kleinem Untertitel, daneben der Teile-Kasten,
        oben rechts ggf. die Fortschrittskarte. Darunter ein Hinweis und die Ansicht, die
        den restlichen Platz der Seite füllt.
        """
        layout = self.layout
        number_block = self.step_number(number, subtitle)
        reserved = number_block.width + (progress.width if progress else 0) + 2 * BLOCK_SPACING
        callout_width = min(
            layout.content_width - reserved, round(layout.content_width * CALLOUT_SHARE)
        )
        callout = self.callout(PartsList.from_placements(step.placements).entries, callout_width)
        header_parts = [number_block, callout]
        header = _side_by_side(header_parts, WHITE, align_top=True)
        if progress is not None:  # Karte rechtsbündig
            header = _spread(header, progress, layout.content_width)
        blocks = [header]
        if hint:
            blocks.append(_text_block(hint, layout.font(BODY_SIZE), layout.content_width))
        used = sum(block.height for block in blocks) + BLOCK_SPACING * len(blocks)
        render = view(layout.content_width, max(100, layout.content_height - used))
        blocks.append(_centered(render, layout.content_width))
        return tuple(blocks)

    def step_number(self, number: int, subtitle: str) -> Block:
        """Große fette Schrittnummer, darunter klein der Untertitel."""
        number_font, small = self.layout.bold(STEP_SIZE), self.layout.font(SMALL_SIZE)
        text_width = round(max(number_font.getlength(str(number)), small.getlength(subtitle)))
        number_height = round(STEP_SIZE * 1.05)
        block = Image.new("RGB", (text_width, number_height + SMALL_SIZE * 2), WHITE)
        draw = ImageDraw.Draw(block)
        draw.text((0, 0), str(number), fill=BLACK, font=number_font)
        draw.text((0, number_height), subtitle, fill=GRAY_TEXT, font=small)
        return block

    def substrate_view(self, width: int, height: int) -> Block:
        """3D-Ansicht des leeren Unterbaus."""
        return render_iso(
            {},
            _covered_area(self.mosaic.substrate),
            self.layout.view,
            underlay=self.underlay,
            max_width=width,
            max_height=height,
            supersample=1,
        )

    def connection_plan(self, width: int, height: int) -> Block:
        """Schema mit nummerierten Paneelen und den Pins an den Nähten."""
        plates = self.mosaic.substrate
        area = _covered_area(plates)
        stud_px = map_stud_px(
            area.cols, area.rows, min(width, height, PLAN_PX), self.layout.max_stud_px
        )
        font = self.layout.font(max(SMALL_SIZE, self.layout.section_studs * stud_px // 3))
        return connection_plan(plates, self.mosaic.connectors, stud_px=stud_px, font=font)

    def full_view(
        self, step: BuildStep, built: Sequence[Placement], width: int, height: int
    ) -> Block:
        """3D-Ansicht des ganzen Mosaiks mit den neuen Teilen hervorgehoben."""
        cells = self.cells([*built, *step.placements], self.full_area, set(step.placements))
        large = self.mosaic.width * self.mosaic.height > LARGE_RENDER_CELLS
        return render_iso(
            cells,
            self.full_area,
            self.layout.view,
            underlay=self.underlay,
            max_width=width,
            max_height=height,
            supersample=1 if large else self.layout.supersample,
        )

    def progress_map(self, section: Section, built: Sequence[Placement]) -> Block:
        """Kleine Karte: was steht schon, welcher Abschnitt kommt jetzt (rot umrandet)."""
        return self.section_map(built, section, PROGRESS_MAP_PX)

    def finished(self) -> Unit:
        """Schlussseite: das ganze Mosaik und was es zusammenhält."""
        mosaic = self.mosaic
        text = f"Alle {len(self.sections)} Abschnitte sind gebaut."
        if mosaic.connectors:
            text += (
                f" Die {len(mosaic.substrate)} Paneele halten mit "
                f"{len(mosaic.connectors)} Technic-Pins zusammen."
            )
        if mosaic.bond:
            text += f" Die Verbindungslage aus {len(mosaic.bond)} Plates hält die Platten zusammen."
        if mosaic.supports:
            text += " Der erhöhte Rahmen überbrückt zusätzlich alle Nähte am Rand."
        if mosaic.relief:
            text += f" {len(mosaic.relief)} Plates geben dem Bild Relief."
        layout = self.layout
        return (
            _text_block("Fertig!", layout.bold(HEADING_SIZE), layout.content_width),
            _text_block(text, layout.font(BODY_SIZE), layout.content_width),
            _centered(self.finished_render, layout.content_width),
        )

    def inventory(self) -> list[Unit]:
        """Teileliste am Ende wie in LEGO-Heften: Teilbild, Stückzahl, Farbe, Element-ID."""
        items = [
            self.icons.item(entry, WHITE, details=self.details(entry))
            for entry in self.parts.entries
        ]
        heading = _text_block(
            "Teileliste", self.layout.bold(HEADING_SIZE), self.layout.content_width
        )
        rows = _grid_rows(items, self.layout.content_width, WHITE)
        return [(heading, rows[0]), *((row,) for row in rows[1:])]

    def details(self, entry: PartsListEntry) -> list[str]:
        """Farbname und LEGO-Element-ID (falls bekannt) unter einem Teil der Teileliste."""
        lines = [entry.color.lego_name or entry.color.name]
        element = self.mosaic.element_ids.get((entry.part.part_num, entry.color.id))
        lines.append(element or entry.part.bricklink_item)
        return lines

    def callout(self, entries: Sequence[PartsListEntry], max_width: int) -> Block:
        """Hellblauer Kasten mit den Teilen eines Schritts."""
        items = [self.icons.item(entry, CALLOUT_FILL) for entry in entries]
        inner_width = max(max_width - 2 * CALLOUT_PADDING, max(item.width for item in items))
        content = _stack(_grid_rows(items, inner_width, CALLOUT_FILL), ICON_GAP // 2, CALLOUT_FILL)
        size = (content.width + 2 * CALLOUT_PADDING, content.height + 2 * CALLOUT_PADDING)
        block = Image.new("RGB", size, WHITE)
        ImageDraw.Draw(block).rounded_rectangle(
            (0, 0, size[0] - 1, size[1] - 1),
            radius=CALLOUT_PADDING,
            fill=CALLOUT_FILL,
            outline=CALLOUT_BORDER,
            width=2,
        )
        block.paste(content, (CALLOUT_PADDING, CALLOUT_PADDING))
        return block

    def cells(
        self, placements: Iterable[Placement], area: Area, highlight: set[Placement] | None = None
    ) -> dict[tuple[int, int], RenderCell]:
        """Zellen innerhalb des Bereichs; Teile aus ``highlight`` werden hervorgehoben.

        Spätere Teile überschreiben frühere in derselben Zelle (der Rahmen liegt auf
        seinen Stützteilen und ist höher, also gewinnt er).
        """
        result: dict[tuple[int, int], RenderCell] = {}
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
                    cell = _cell_content(content, placement, row, col, result.get((row, col)))
                    if cell is not None:
                        result[(row, col)] = cell
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
            font=self.layout.bold(max(SMALL_SIZE, self.layout.section_studs * stud_px // 3)),
            background=UNBUILT_RGB,
            current=current,
        )


def _cell_content(
    content: RenderCell, placement: Placement, row: int, col: int, below: RenderCell | None
) -> RenderCell | None:
    """Inhalt einer Zelle; Formteile bekommen ihren Umriss und behalten, was darunter liegt.

    ``None``: Das Formteil berührt die Zelle nicht (z. B. die freie Ecke eines Dreiecks).
    """
    part = placement.part
    if not part.is_shaped:
        return content
    d_row, d_col = row - placement.row, col - placement.col
    outline = cell_outline(part, placement.turns, d_row, d_col)
    if outline is None:
        return None
    has_stud = part.kind is not PartKind.TILE and stud_visible(part, placement.turns, d_row, d_col)
    return replace(content, outline=outline, has_stud=has_stud, below=below)


def _section_subtitle(step: BuildStep, section: Section, sections: int) -> str:
    """Z. B. „Abschnitt 3/9 · Zeilen 1–4“ oder „Abschnitt 3/9 · Relief, Lage 1“."""
    prefix = f"Abschnitt {section.number}/{sections}"
    if step.kind is StepKind.RELIEF:
        return f"{prefix} · Relief, Lage {step.level + 1}"
    first_row = min(p.row for p in step.placements) + 1
    last_row = max(p.row + p.rows_spanned for p in step.placements)
    return f"{prefix} · Zeilen {first_row}–{last_row}"


def _base_title(step: BuildStep) -> str:
    """Untertitel der Schritte vor dem Bild."""
    match step.kind:
        case StepKind.SUBSTRATE:
            return _SUBSTRATE_TITLES.get(step.placements[0].part.kind, "Unterbau auslegen")
        case StepKind.CONNECTORS:
            return "Paneele mit Technic-Pins verbinden"
        case StepKind.BOND:
            return "Platten verbinden"
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
    """Bereich, den die Unterbau-Platten zusammen abdecken."""
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

    def item(
        self, entry: PartsListEntry, background: RgbTuple, *, details: Sequence[str] = ()
    ) -> Block:
        """Teilbild mit „3x“ darunter (wie bei LEGO), optional weitere kleine Zeilen."""
        icon = self.icon(entry.part, entry.color, background)
        lines = [(f"{entry.quantity}x", self.layout.bold(QUANTITY_SIZE), BLACK)]
        lines += [(text, self.layout.font(SMALL_SIZE), GRAY_TEXT) for text in details]
        width = max(icon.width, *(round(font.getlength(text)) for text, font, _ in lines))
        line_height = round(QUANTITY_SIZE * LINE_SPACING)
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
    turns = 0
    if part.is_shaped:  # Formteile in LDraw-Lage, hochkant liegende eine Vierteldrehung weiter
        turns = 1 if box(part)[0] > box(part)[1] else 0
        area = Area(0, 0, *box(part, turns))
    else:
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
    icon_part = Placement(part, color, 0, 0, turns=turns)
    cells = {
        (row, col): cell
        for row in range(area.rows)
        for col in range(area.cols)
        if (cell := _cell_content(content, icon_part, row, col, None)) is not None
    }
    return render_iso(
        cells,
        area,
        layout.view,
        underlay=None,
        max_width=ICON_MAX_PX,
        max_height=ICON_MAX_PX,
        supersample=layout.supersample,
        # gleicher Maßstab für alle Teile; nur sehr große Plates werden eingepasst
        scale=ICON_SCALE if part.length_studs <= LARGE_PART_STUDS else None,
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


def _badge(page: Image.Image, origin: tuple[int, int], text: str, font: Font) -> int:
    """Gelbes Etikett mit dunkler Schrift (wie die Serienkennung auf LEGO-Boxen); Höhe."""
    draw = ImageDraw.Draw(page)
    padding = BADGE_SIZE // 2
    width = round(font.getlength(text)) + 2 * padding
    height = BADGE_SIZE + 2 * padding
    left, top = origin
    draw.rounded_rectangle((left, top, left + width, top + height), radius=6, fill=COVER_ACCENT)
    draw.text(
        (left + padding, top + height / 2), text, fill=COVER_BACKGROUND, font=font, anchor="lm"
    )
    return height


def _fitting_font(layout: PageLayout, text: str, width: int, size: int) -> Font:
    """Größte fette Schrift bis ``size``, bei der das längste Wort in ``width`` passt."""
    longest = max(text.split() or [text], key=len)
    while size > BODY_SIZE and layout.bold(size).getlength(longest) > width:
        size -= 4
    return layout.bold(size)


def _font_size(font: Font) -> int:
    """Schriftgröße in Pixeln (Pillows Ersatzschrift kennt keine, dann die Textgröße)."""
    return round(font.size) if isinstance(font, ImageFont.FreeTypeFont) else BODY_SIZE


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


def _side_by_side(
    items: Sequence[Block], background: RgbTuple, *, align_top: bool = False
) -> Block:
    """Blöcke nebeneinander, unten bündig (Teilbilder) oder oben bündig (Kopfzeilen)."""
    width = sum(item.width for item in items) + ICON_GAP * (len(items) - 1)
    height = max(item.height for item in items)
    row = Image.new("RGB", (width, height), background)
    left = 0
    for item in items:
        row.paste(item, (left, 0 if align_top else height - item.height))
        left += item.width + ICON_GAP
    return row


def _spread(left: Block, right: Block, width: int) -> Block:
    """Zwei Blöcke auf voller Breite: einer links, einer rechts (oben bündig)."""
    block = Image.new("RGB", (width, max(left.height, right.height)), WHITE)
    block.paste(left, (0, 0))
    block.paste(right, (width - right.width, 0))
    return block


def _centered(item: Block, width: int) -> Block:
    """Block waagerecht mittig auf ``width`` Pixeln."""
    if item.width >= width:
        return item
    block = Image.new("RGB", (width, item.height), WHITE)
    block.paste(item, ((width - item.width) // 2, 0))
    return block


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
    line_height = round(_font_size(font) * LINE_SPACING)
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


def paginate(
    document: Sequence[Unit | PageBreak | FullPage], layout: PageLayout
) -> list[Image.Image]:
    """Verteilt die Einheiten auf Seiten; eine Einheit wird nie getrennt.

    Ist eine einzelne Einheit höher als eine Seite, wird diese Seite entsprechend höher.
    Fertige Seiten (``FullPage``) kommen unverändert und ohne Seitenzahl dazwischen.
    """
    pages: list[list[Block] | FullPage] = []
    current: list[Block] = []
    used = 0
    for item in document:
        if isinstance(item, PageBreak | FullPage):
            if current:
                pages.append(current)
            current, used = [], 0
            if isinstance(item, FullPage):
                pages.append(item)
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
    return [
        page.image if isinstance(page, FullPage) else _compose(page, number, layout)
        for number, page in enumerate(pages, start=1)
    ]


def _compose(blocks: Sequence[Block], number: int, layout: PageLayout) -> Image.Image:
    """Setzt die Blöcke untereinander auf eine Seite; Seitenzahl unten rechts wie bei LEGO."""
    content_height = sum(block.height for block in blocks) + BLOCK_SPACING * (len(blocks) - 1)
    height = max(layout.height, content_height + 2 * layout.margin + PAGE_NUMBER_SIZE)
    page = Image.new("RGB", (layout.width, height), WHITE)
    top = layout.margin
    for block in blocks:
        page.paste(block, (layout.margin, top))
        top += block.height + BLOCK_SPACING
    draw = ImageDraw.Draw(page)
    font = layout.bold(PAGE_NUMBER_SIZE)
    text = str(number)
    padding = PAGE_NUMBER_SIZE // 3
    right, bottom = layout.width - layout.margin, height - layout.margin // 2
    box_width = round(font.getlength(text)) + 4 * padding
    draw.rounded_rectangle(
        (right - box_width, bottom - PAGE_NUMBER_SIZE - 2 * padding, right, bottom),
        radius=8,
        fill=PAGE_NUMBER_FILL,
    )
    draw.text(
        (right - box_width / 2, bottom - PAGE_NUMBER_SIZE / 2 - padding),
        text,
        fill=BLACK,
        font=font,
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
