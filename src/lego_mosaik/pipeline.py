"""Pipeline: orchestriert alle Schritte vom Bild zum fertigen Mosaik.

Die Pipeline kennt nur Protocols und die Domain. Welche konkreten Klassen
dahinterstecken, entscheidet ``main.py`` (Composition Root) über die
``component_factory``. So bleibt die Pipeline unverändert, wenn z. B. ein neues
Dithering-Verfahren oder eine PostgreSQL-Quelle dazukommt.
"""

import logging
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    apply_overrides,
    config_to_dict,
    frame_part_kinds,
    substrate_color_id,
)
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import (
    LayoutSize,
    apply_frame,
    complete_size,
    connector_placements,
    custom_layout_size,
    find_connector,
    find_substrate_part,
    frame_mask,
    grid_layout_size,
    require_part_color,
    require_single_stud_color,
    running_bond_offset,
    substrate_placements,
)
from lego_mosaik.domain.models import (
    EMPTY_CELL,
    PLATE_HEIGHT_LDU,
    SUBSTRATE_PART_KINDS,
    IndexGrid,
    LegoColor,
    Mosaic,
    PartCatalog,
    PartKind,
    PartType,
    Placement,
    Substrate,
    TilingStats,
)
from lego_mosaik.domain.palette import build_palette, most_used_colors
from lego_mosaik.domain.sections import split_sections
lazy from lego_mosaik.dithering.protocol import ColorSpace, Ditherer
lazy from lego_mosaik.imaging.protocol import ImageInfo, ImagePreparer, RgbImage
lazy from lego_mosaik.outputs.protocol import OutputWriter
lazy from lego_mosaik.sources.protocol import (
    CatalogUpdaterProtocol,
    FileInfo,
    PartCatalogSource,
    UpdateReport,
)
lazy from lego_mosaik.tiling.protocol import TilingStrategy

logger = logging.getLogger(__name__)

# ergebnis.json listet alle erzeugten Dateien auf und muss deshalb zuletzt laufen
FINAL_WRITER_NAME = "result_json"
TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"
# Erlaubte Zeichen im Ordnernamen; alles andere wird zu "_" (Schutz vor seltsamen Dateinamen)
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")
DEFAULT_TITLE = "mosaik"

type StepCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class PipelineComponents:
    """Die austauschbaren Bausteine eines Laufs (von ``main.py`` gebaut).

    Attributes:
        tiler: Zerlegt das Bild.
        frame_tiler: Zerlegt den Rahmen (eigene Teilarten, z. B. nur Tiles).
        support_tiler: Zerlegt die Plates unter einem erhöhten Rahmen.
    """

    image_preparer: ImagePreparer
    color_space: ColorSpace
    ditherer: Ditherer
    tiler: TilingStrategy
    frame_tiler: TilingStrategy
    support_tiler: TilingStrategy
    writers: tuple[OutputWriter, ...]


@dataclass(frozen=True, slots=True)
class MosaicResult:
    """Ergebnis eines Laufs.

    Attributes:
        id: Name des Ausgabeordners (dient auch als ID in der API).
        mosaic: Das fertige Mosaik.
        target_dir: Ordner mit allen erzeugten Dateien.
        files: Writer-Name → Pfad der Hauptdatei.
        stats: Kennzahlen (Größe, Farben, Teile, Einsparung, Dauer pro Schritt).
    """

    id: str
    mosaic: Mosaic
    target_dir: Path
    files: dict[str, Path]
    stats: dict[str, object]


@dataclass(slots=True)
class _Timer:
    """Misst die Dauer benannter Schritte und meldet sie an einen Callback."""

    on_step: StepCallback | None
    durations: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def step(self, label: str) -> Iterator[None]:
        """Context Manager: ``with timer.step("Dithering"): …`` misst den Block."""
        logger.info("%s …", label)
        if self.on_step is not None:
            self.on_step(label)
        start = time.perf_counter()
        yield  # hier läuft der Code im with-Block
        self.durations[label] = round(time.perf_counter() - start, 3)


class MosaicPipeline:
    """Vom Bild zum Mosaik: Katalog → Palette → Bild → Dithering → Rahmen → Zerlegung → Ausgaben.

    Args:
        config: Standardkonfiguration; Overrides gelten nur für einzelne Läufe.
        catalog_source: Liefert den Teilekatalog.
        updater: Aktualisiert die Katalogdateien.
        component_factory: Baut aus der Laufkonfiguration die konkreten Bausteine.
        method_names: Registrierte Namen je Art (für ``verfahren``), z. B. ``{"dithering": …}``.
        now: Uhr für den Ordnernamen (in Tests austauschbar).
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        catalog_source: PartCatalogSource,
        updater: CatalogUpdaterProtocol,
        component_factory: Callable[[AppConfig], PipelineComponents],
        method_names: Mapping[str, Sequence[str]],
        now: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.config = config
        self._source = catalog_source
        self._updater = updater
        self._component_factory = component_factory
        self.method_names = {kind: tuple(names) for kind, names in method_names.items()}
        self._now = now

    # --- Katalog ---------------------------------------------------------------

    def update_catalog(self, *, force: bool = False) -> UpdateReport:
        """Aktualisiert die Katalogdateien und verwirft danach den Cache."""
        report = self._updater.update(force=force)
        if report.downloaded_any:
            self._source.invalidate()
        return report

    def catalog_status(self) -> tuple[FileInfo, ...]:
        """Stand der lokalen Katalogdateien."""
        return self._updater.status()

    def load_catalog(self, run_config: AppConfig | None = None) -> PartCatalog:
        """Lädt den Katalog; bei ``auto_update`` werden veraltete Dateien vorher erneuert.

        Raises:
            DownloadError: Wenn Dateien fehlen und nicht geladen werden können.
        """
        if (run_config or self.config).source.auto_update:
            self.update_catalog().raise_if_unusable()
        return self._source.load_catalog()

    def palette(self, overrides: MosaicOverrides | None = None) -> tuple[LegoColor, ...]:
        """Palette für die (ggf. überschriebenen) Teilarten und Farbkategorien."""
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        return _palette(self.load_catalog(run_config), run_config)

    def inspect_image(self, image: Path | bytes) -> ImageInfo:
        """Prüft ein Bild und liefert Größe und Format (z. B. für den interaktiven Modus).

        Raises:
            InvalidImageError: Wenn die Datei fehlt oder kein lesbares Bild ist.
        """
        return self._component_factory(self.config).image_preparer.inspect(image)

    # --- Mosaik erzeugen -------------------------------------------------------

    def preview(
        self,
        image: Path | bytes,
        overrides: MosaicOverrides | None = None,
        *,
        title: str = DEFAULT_TITLE,
        on_step: StepCallback | None = None,
    ) -> Mosaic:
        """Berechnet das Mosaik, ohne Dateien zu schreiben (schnelle Vorschau).

        Raises:
            LegoMosaicError: Bei ungültiger Eingabe oder Verarbeitungsfehlern.
        """
        return self._build(image, overrides, title, _Timer(on_step)).mosaic

    def run(
        self,
        image: Path | bytes,
        overrides: MosaicOverrides | None = None,
        *,
        title: str = DEFAULT_TITLE,
        on_step: StepCallback | None = None,
    ) -> MosaicResult:
        """Erzeugt ein Mosaik und alle konfigurierten Ausgaben.

        Args:
            image: Bilddatei oder Bildinhalt.
            overrides: Werte, die nur für diesen Lauf gelten.
            title: Titel des Mosaiks (z. B. Dateiname ohne Endung).
            on_step: Wird zu Beginn jedes Schritts mit dessen deutscher Bezeichnung aufgerufen.

        Raises:
            LegoMosaicError: Bei ungültiger Eingabe oder Verarbeitungsfehlern.
        """
        timer = _Timer(on_step)
        build = self._build(image, overrides, title, timer)
        target_dir = self._create_target_dir(build.config.output_dir, title)
        with timer.step("Ausgaben schreiben"):
            files = _write_outputs(build.components.writers, build.mosaic, target_dir)
        build.stats["dauer_sekunden"] = dict(timer.durations)
        return MosaicResult(target_dir.name, build.mosaic, target_dir, files, build.stats)

    def _build(
        self,
        image: Path | bytes,
        overrides: MosaicOverrides | None,
        title: str,
        timer: _Timer,
    ) -> _Build:
        """Alle Rechenschritte bis zum fertigen Mosaik (ohne Dateien)."""
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        components = self._component_factory(run_config)
        with timer.step("Teilekatalog laden"):
            catalog = self.load_catalog(run_config)
            palette = _palette(catalog, run_config)
            materials = _Materials.resolve(catalog, run_config)
        with timer.step("Bild laden und einpassen"):
            size = _layout_size(run_config, components.image_preparer, image)
            rgb = components.image_preparer.prepare(
                image,
                width=size.image_width,
                height=size.image_height,
                pad_color=materials.pad_color.rgb,
            )
        with timer.step("Dithering"):
            grid, palette = _dither(components, rgb, palette, run_config.palette.max_colors)
            grid, palette = apply_frame(
                grid,
                palette,
                run_config.frame.width,
                materials.frame_color,
                materials.corner_color,
            )
        with timer.step("Zerlegung in Teile"):
            layers = _tile_layers(components, grid, palette, catalog, run_config)
            substrate, connectors = _substrate_layers(materials, size, run_config)
        stats = _stats(size, layers, substrate, connectors, timer.durations)
        mosaic = Mosaic(
            title=title,
            width=size.width,
            height=size.height,
            color_grid=grid,
            palette=palette,
            placements=layers.image,
            frame=layers.frame,
            supports=layers.supports,
            substrate=substrate,
            connectors=connectors,
            element_ids=catalog.element_ids,
            metadata={"konfiguration": config_to_dict(run_config), "statistik": stats},
        )
        return _Build(run_config, components, mosaic, stats)

    def _create_target_dir(self, output_dir: Path, title: str) -> Path:
        """Legt ``<ausgabe>/<zeitstempel>_<titel>`` an; bei Kollision mit Zähler."""
        base = f"{self._now().strftime(TIMESTAMP_FORMAT)}_{safe_name(title)}"
        candidate, counter = output_dir / base, 2
        while candidate.exists():
            candidate, counter = output_dir / f"{base}_{counter}", counter + 1
        candidate.mkdir(parents=True)
        return candidate


@dataclass(frozen=True, slots=True)
class _Build:
    """Zwischenergebnis eines Laufs: Konfiguration, Bausteine, Mosaik und Kennzahlen."""

    config: AppConfig
    components: PipelineComponents
    mosaic: Mosaic
    stats: dict[str, object]


@dataclass(frozen=True, slots=True)
class _Materials:
    """Farben und Unterbau-Teile eines Laufs, gegen den Katalog geprüft.

    Alle Prüfungen passieren vor dem Laden des Bilds, damit Tippfehler in Farb-IDs
    sofort mit einer deutschen Meldung auffallen.
    """

    frame_color: LegoColor
    corner_color: LegoColor | None
    pad_color: LegoColor
    substrate: tuple[PartType, LegoColor] | None
    connector: tuple[PartType, LegoColor] | None

    @classmethod
    def resolve(cls, catalog: PartCatalog, config: AppConfig) -> _Materials:
        """Prüft Rahmen-, Ecken-, Rand- und Unterbaufarben (deutsche Fehlermeldungen)."""
        frame, kinds = config.frame, frame_part_kinds(config)
        frame_color = require_single_stud_color(catalog, frame.color_id, kinds, "Rahmenfarbe")
        if frame.width and frame.raise_plates:  # Stützlagen brauchen Plates in der Rahmenfarbe
            require_single_stud_color(
                catalog, frame.color_id, (PartKind.PLATE,), "Rahmenfarbe (Erhöhung, Plates)"
            )
        corner_color = None
        if frame.corner_color_id is not None:
            corner_color = require_single_stud_color(
                catalog, frame.corner_color_id, kinds, "Eckenfarbe"
            )
        pad_color = catalog.colors.get(config.mosaic.pad_color_id)
        if pad_color is None:
            raise ConfigError(f"Die Randfarbe {config.mosaic.pad_color_id} ist unbekannt.")
        substrate, connector = _substrate_materials(catalog, config)
        return cls(frame_color, corner_color, pad_color, substrate, connector)


def _substrate_materials(
    catalog: PartCatalog, config: AppConfig
) -> tuple[tuple[PartType, LegoColor] | None, tuple[PartType, LegoColor] | None]:
    """Unterbau-Platte und (bei Art-Paneelen) Verbinder samt geprüfter Farbe."""
    layout = config.layout
    if layout.substrate is Substrate.NONE:
        return None, None
    part = find_substrate_part(catalog, SUBSTRATE_PART_KINDS[layout.substrate], layout.plate_studs)
    substrate = part, require_part_color(catalog, part, substrate_color_id(layout))
    if layout.substrate is not Substrate.ART_PANEL:
        return substrate, None
    pin = find_connector(catalog)
    return substrate, (pin, require_part_color(catalog, pin, layout.connector_color_id))


def _layout_size(config: AppConfig, preparer: ImagePreparer, image: Path | bytes) -> LayoutSize:
    """Größe aus Platten oder frei; eine fehlende Seite kommt aus dem Seitenverhältnis."""
    layout, frame = config.layout, config.frame.width
    if not layout.custom_size:
        size = grid_layout_size(layout.plate_studs, layout.plates_wide, layout.plates_high, frame)
    else:
        width, height = layout.width_studs, layout.height_studs
        if width is None or height is None:
            ratio = preparer.inspect(image).aspect_ratio
            width, height = complete_size(width, height, ratio, frame)
            logger.info("Größe aus dem Seitenverhältnis: %d × %d Noppen.", width, height)
        size = custom_layout_size(width, height, layout.plate_studs, frame)
    if layout.substrate is Substrate.BASEPLATE and size.plates_wide * size.plates_high > 1:
        logger.warning(
            "Mehrere Grundplatten liegen nur lose nebeneinander. Für ein stabiles Bild "
            "„--unterbau art“ (LEGO-Art-Paneele mit Technic-Pins) verwenden."
        )
    return size


@dataclass(frozen=True, slots=True)
class _Layers:
    """Zerlegte Lagen: Bild (pro Abschnitt), Rahmen und Stützteile darunter."""

    image: tuple[Placement, ...]
    frame: tuple[Placement, ...]
    supports: tuple[Placement, ...]


def _tile_layers(
    components: PipelineComponents,
    grid: IndexGrid,
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    config: AppConfig,
) -> _Layers:
    """Zerlegt Bild und Rahmen getrennt; ein erhöhter Rahmen bekommt Stützlagen.

    Der Rahmen wird am Stück zerlegt (seine Teile dürfen Abschnittsgrenzen und
    Plattennähte überbrücken), das Bild pro Abschnitt.
    """
    height, width = grid.shape
    ring = frame_mask(height, width, config.frame.width)
    section = config.instructions.section_studs
    size = section if config.tiling.split_sections else max(height, width)
    # np.where(Bedingung, a, b): pro Zelle a, wo die Bedingung gilt, sonst b
    image_grid = np.where(ring, EMPTY_CELL, grid).astype(np.intp)
    image = _tile_sections(components.tiler, image_grid, palette, catalog, size)
    if not ring.any():
        return _Layers(image, (), ())
    raise_ldu = config.frame.raise_plates * PLATE_HEIGHT_LDU
    frame_grid = np.where(ring, grid, EMPTY_CELL).astype(np.intp)
    frame = tuple(
        replace(placement, bottom_ldu=raise_ldu)
        for placement in components.frame_tiler.tile(frame_grid, palette, catalog)
    )
    supports = _supports(components.support_tiler, ring, palette, catalog, config)
    return _Layers(image, frame, supports)


def _supports(
    tiler: TilingStrategy,
    ring: npt.NDArray[np.bool_],
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    config: AppConfig,
) -> tuple[Placement, ...]:
    """Plates unter einem erhöhten Rahmen, „auf Lücke“ gelegt (Mauerverband).

    Die Abschnitte für die Zerlegung sind gegenüber den Nähten des Unterbaus
    verschoben (``running_bond_offset``). Weil kein Teil eine Abschnittsgrenze
    überquert, liegen die Fugen der Stützlage nie auf einer Naht: Die Stützteile
    überbrücken die Nähte und halten die Platten am Rand zusammen.
    """
    if config.frame.raise_plates == 0:
        return ()
    frame_color = catalog.colors[config.frame.color_id]
    grid = np.where(ring, palette.index(frame_color), EMPTY_CELL).astype(np.intp)
    seam = config.layout.plate_studs
    lengths = [
        part.length_studs
        for part in catalog.parts_in_color(frame_color.id, (PartKind.PLATE,))
        if part.length_studs <= config.tiling.max_part_length
    ]
    offset = running_bond_offset(seam, max(lengths, default=1))
    layer = _tile_sections(tiler, grid, palette, catalog, seam, offset=offset)
    return tuple(
        replace(placement, bottom_ldu=level * PLATE_HEIGHT_LDU)
        for level in range(config.frame.raise_plates)
        for placement in layer
    )


def _substrate_layers(
    materials: _Materials, size: LayoutSize, config: AppConfig
) -> tuple[tuple[Placement, ...], tuple[Placement, ...]]:
    """Unterbau-Platten und die Pins, die benachbarte Art-Paneele verbinden."""
    if materials.substrate is None:
        return (), ()
    part, color = materials.substrate
    plates = substrate_placements(part, color, size.plates_wide, size.plates_high)
    if materials.connector is None:
        return plates, ()
    pin, pin_color = materials.connector
    layout = config.layout
    pins = connector_placements(pin, pin_color, size, layout.plate_studs, layout.pins_per_seam)
    return plates, pins


def _palette(catalog: PartCatalog, config: AppConfig) -> tuple[LegoColor, ...]:
    """Palette laut Konfiguration; leer ist ein Konfigurationsfehler."""
    palette = build_palette(
        catalog,
        config.palette.part_kinds,
        categories=config.palette.color_categories,
        include_color_ids=config.palette.include_color_ids,
        exclude_color_ids=config.palette.exclude_color_ids,
    )
    if not palette:
        raise ConfigError(
            "Mit den gewählten Teilarten und Farbkategorien bleibt keine Farbe übrig."
        )
    return palette


def _dither(
    components: PipelineComponents,
    rgb: RgbImage,
    palette: tuple[LegoColor, ...],
    max_colors: int | None,
) -> tuple[IndexGrid, tuple[LegoColor, ...]]:
    """Dithert das Bild; mit ``max_colors`` ein zweites Mal mit den häufigsten Farben."""
    image_lab = components.color_space.to_lab(rgb.astype(np.float64))

    def dither_with(colors: tuple[LegoColor, ...]) -> IndexGrid:
        palette_lab = components.color_space.to_lab(
            np.array([c.rgb for c in colors], dtype=np.float64)
        )
        return components.ditherer.dither(image_lab, palette_lab)

    grid = dither_with(palette)
    if max_colors is not None and len(palette) > max_colors:
        palette = most_used_colors(grid, palette, max_colors)
        logger.info("Palette auf die %d häufigsten Farben begrenzt.", len(palette))
        grid = dither_with(palette)
    return grid, palette


def _tile_sections(
    tiler: TilingStrategy,
    grid: IndexGrid,
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    section_studs: int,
    *,
    offset: int = 0,
) -> tuple[Placement, ...]:
    """Zerlegt jeden Abschnitt einzeln und verschiebt die Teile an ihre echte Position.

    Funktioniert mit jeder Zerlegungsstrategie, weil jede nur ein (kleineres) Raster sieht.
    """
    height, width = grid.shape
    placements: list[Placement] = []
    for section in split_sections(width, height, section_studs, offset=offset):
        # Slicing schneidet den Abschnitt aus; die Strategie sieht nur dieses Teilraster
        sub_grid = grid[
            section.row : section.row + section.rows, section.col : section.col + section.cols
        ]
        placements += [
            replace(p, row=p.row + section.row, col=p.col + section.col)
            for p in tiler.tile(sub_grid, palette, catalog)
        ]
    return tuple(placements)


def _stats(
    size: LayoutSize,
    layers: _Layers,
    substrate: Sequence[Placement],
    connectors: Sequence[Placement],
    durations: Mapping[str, float],
) -> dict[str, object]:
    """Kennzahlen des Laufs als JSON-fähiges dict."""
    surface = layers.frame + layers.image
    tiling = TilingStats.from_placements(size.width, size.height, surface)
    return {
        "breite": size.width,
        "hoehe": size.height,
        "bild_breite": size.image_width,
        "bild_hoehe": size.image_height,
        "farben": len({placement.color.id for placement in surface}),
        **asdict(tiling),  # pixels, parts, savings_percent
        "stuetzteile": len(layers.supports),
        "unterbau_platten": len(substrate),
        "verbinder": len(connectors),
        "dauer_sekunden": dict(durations),
    }


def _write_outputs(
    writers: Sequence[OutputWriter], mosaic: Mosaic, target_dir: Path
) -> dict[str, Path]:
    """Ruft alle Writer auf; ``result_json`` zuletzt, damit es alle Dateien sieht."""
    # sorted ist stabil (übrige Reihenfolge bleibt);
    # der Schlüssel False < True schiebt den Final-Writer nach hinten
    ordered = sorted(writers, key=lambda writer: writer.name == FINAL_WRITER_NAME)
    return {writer.name: writer.write(mosaic, target_dir) for writer in ordered}


def safe_name(title: str) -> str:
    """Macht aus einem Titel einen sicheren Ordnernamen ("Katze & Hund" → "Katze_Hund")."""
    cleaned = UNSAFE_NAME_CHARS.sub("_", title).strip("_")
    return cleaned or DEFAULT_TITLE
