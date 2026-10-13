"""Pipeline: orchestriert alle Schritte vom Bild zum fertigen Mosaik.

Die Pipeline kennt nur Protocols und die Domain. Welche konkreten Klassen
dahinterstecken, entscheidet ``main.py`` (Composition Root) über die
``component_factory``. So bleibt die Pipeline unverändert, wenn z. B. ein neues
Dithering-Verfahren oder eine PostgreSQL-Quelle dazukommt.
"""

import json
import logging
import math
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime
from pathlib import Path

lazy import numpy as np
lazy import numpy.typing as npt

from lego_mosaik.config import (
    AppConfig,
    MosaicOverrides,
    OverrideInput,
    apply_overrides,
    config_to_dict,
    frame_part_kinds,
    parse_overrides,
    relief_color_id,
    substrate_color_id,
)
from lego_mosaik.domain.cleanup import (
    majority_colors,
    majority_filter,
    merge_small_regions,
    rare_colors,
    remove_isolated,
)
from lego_mosaik.domain.editing import GridEdit, apply_edits, edit_from_json, edit_to_json
from lego_mosaik.domain.errors import ConfigError, NotFoundError
from lego_mosaik.domain.layout import (
    BOND_PART_STUDS,
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
    SMOOTHING_LEVELS,
    SUBSTRATE_PART_KINDS,
    BackgroundMode,
    BoolGrid,
    FloatImage,
    IndexGrid,
    LegoColor,
    Mosaic,
    PartCatalog,
    PartKind,
    PartType,
    Placement,
    ReliefMode,
    RgbTuple,
    Substrate,
    TilingStats,
)
from lego_mosaik.domain.palette import build_palette, most_used_colors
from lego_mosaik.domain.relief import relief_levels
from lego_mosaik.domain.sections import split_sections
lazy from lego_mosaik.dithering.protocol import ColorSpace, Ditherer
lazy from lego_mosaik.imaging.protocol import (
    BackgroundReport,
    ImageInfo,
    ImagePreparer,
    RgbImage,
)
lazy from lego_mosaik.outputs.protocol import OutputWriter
lazy from lego_mosaik.shaping.protocol import ShapeRefiner, ShapeResult
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
MAX_BOND_ATTEMPTS = 8  # so viele Versätze probiert die Verbindungslage höchstens
# str.maketrans: Tabelle Zeichen → Ersatz für str.translate
UMLAUTS = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"}
)

# Was jeder Lauf zusätzlich aufhebt, damit man das Mosaik später bearbeiten kann
INPUTS_FILE = "eingaben.json"
SOURCE_STEM = "original"
INPUTS_VERSION = 1
# Pillow-Format → Dateiendung des gespeicherten Originalbilds
IMAGE_SUFFIXES = {"JPEG": ".jpg", "MPO": ".jpg", "PNG": ".png", "WEBP": ".webp", "GIF": ".gif"}

type StepCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class BackgroundResult:
    """Erkannter Hintergrund eines Bilds plus die nächste LEGO-Farbe aus der Palette.

    Attributes:
        report: Farbe, Anteil und Maske (PNG) aus der Bildaufbereitung.
        lego_color: Nächste Palettenfarbe zur Hintergrundfarbe (Vorschlag für „entfernen“).
    """

    report: BackgroundReport
    lego_color: LegoColor


@dataclass(frozen=True, slots=True)
class PipelineComponents:
    """Die austauschbaren Bausteine eines Laufs (von ``main.py`` gebaut).

    Attributes:
        tiler: Zerlegt das Bild.
        frame_tiler: Zerlegt den Rahmen (eigene Teilarten, z. B. nur Tiles).
        support_tiler: Zerlegt die Plates unter einem erhöhten Rahmen und unter dem Relief.
        brick_tiler: Zerlegt die Bricks im Relief (ein Brick ersetzt drei Lagen Plates).
        bond_tiler: Zerlegt die Verbindungslage über großen Platten (lange Plates erlaubt).
        shaper: Wählt Formteile (Stil des Bilds).
    """

    image_preparer: ImagePreparer
    color_space: ColorSpace
    ditherer: Ditherer
    tiler: TilingStrategy
    frame_tiler: TilingStrategy
    support_tiler: TilingStrategy
    brick_tiler: TilingStrategy
    bond_tiler: TilingStrategy
    shaper: ShapeRefiner
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
        shaper = self._component_factory(run_config).shaper
        return _palette(self.load_catalog(run_config), run_config, shaper)

    def inspect_image(self, image: Path | bytes) -> ImageInfo:
        """Prüft ein Bild und liefert Größe und Format (z. B. für den interaktiven Modus).

        Raises:
            InvalidImageError: Wenn die Datei fehlt oder kein lesbares Bild ist.
        """
        return self._component_factory(self.config).image_preparer.inspect(image)

    def check(self, overrides: MosaicOverrides) -> AppConfig:
        """Prüft Overrides vollständig (auch Registry-Namen), ohne ein Bild zu brauchen.

        Raises:
            ConfigError: Bei ungültigen Werten.
        """
        run_config = apply_overrides(self.config, overrides)
        self._component_factory(run_config)  # löst Dithering, Stil, Ausgaben usw. auf
        return run_config

    def colors(self, color_ids: Iterable[int]) -> tuple[LegoColor, ...]:
        """Farben zu Rebrickable-IDs (z. B. für eine eigene Palette).

        Raises:
            ConfigError: Bei unbekannten IDs.
        """
        known = self.load_catalog().colors
        unknown = [
            str(cid) for cid in color_ids if cid not in known or known[cid].ldraw_code is None
        ]
        if unknown:
            raise ConfigError(f"Unbekannte Farb-IDs: {', '.join(unknown)} (siehe „farben“).")
        return tuple(known[cid] for cid in color_ids)

    def edited_image(
        self, image: Path | bytes, overrides: MosaicOverrides | None = None, *, max_px: int
    ) -> bytes:
        """Das bearbeitete Bild vor dem Mosaik (Filter, Ausschnitt, Hintergrund) als PNG."""
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        catalog = self.load_catalog(run_config)
        pad = _pad_color(catalog, run_config)
        background = _background_color(catalog, run_config, None) or pad
        return self._component_factory(run_config).image_preparer.edited(
            image, max_px=max_px, pad_color=pad.rgb, background_color=background.rgb
        )

    def detect_background(
        self, image: Path | bytes, overrides: MosaicOverrides | None = None, *, max_px: int
    ) -> BackgroundResult:
        """Erkennt den Hintergrund und schlägt die nächste LEGO-Farbe der Palette vor."""
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        components = self._component_factory(run_config)
        catalog = self.load_catalog(run_config)
        report = components.image_preparer.detect_background(
            image, pad_color=_pad_color(catalog, run_config).rgb, max_px=max_px
        )
        palette = _palette(catalog, run_config, components.shaper)
        to_lab = components.color_space.to_lab
        target = to_lab(np.array([report.color], dtype=np.float64))[0]
        distances = np.linalg.norm(
            to_lab(np.array([c.rgb for c in palette], dtype=np.float64)) - target, axis=-1
        )
        return BackgroundResult(report, palette[int(distances.argmin())])

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
        edits: Sequence[GridEdit] = (),
        inputs: OverrideInput | None = None,
        base: str | None = None,
    ) -> MosaicResult:
        """Erzeugt ein Mosaik und alle konfigurierten Ausgaben.

        Args:
            image: Bilddatei oder Bildinhalt.
            overrides: Werte, die nur für diesen Lauf gelten.
            title: Titel des Mosaiks (z. B. Dateiname ohne Endung).
            on_step: Wird zu Beginn jedes Schritts mit dessen deutscher Bezeichnung aufgerufen.
            edits: Umgefärbte Noppen und Flächen (siehe ``domain/editing.py``).
            inputs: Die rohen Eingaben hinter ``overrides``; mit ``storage.keep_source``
                landen sie samt Originalbild im Ordner, damit ``edit`` neu rechnen kann.
            base: ID des Mosaiks, aus dem dieses durch Bearbeiten entstanden ist.

        Raises:
            LegoMosaicError: Bei ungültiger Eingabe oder Verarbeitungsfehlern.
        """
        timer = _Timer(on_step)
        build = self._build(image, overrides, title, timer, edits)
        if base is not None:
            mosaic = replace(build.mosaic, metadata={**build.mosaic.metadata, "basis": base})
            build = replace(build, mosaic=mosaic)
        target_dir = self._create_target_dir(build.config.output_dir, title)
        with timer.step("Ausgaben schreiben"):
            if build.config.storage.keep_source:
                image_format = build.components.image_preparer.inspect(image).format
                _keep_source(target_dir, image, image_format, inputs, title, edits, base)
            files = _write_outputs(build.components.writers, build.mosaic, target_dir)
        build.stats["dauer_sekunden"] = dict(timer.durations)
        return MosaicResult(target_dir.name, build.mosaic, target_dir, files, build.stats)

    def edit(
        self,
        directory: Path,
        edits: Sequence[GridEdit],
        *,
        required_outputs: Sequence[str] = (),
        on_step: StepCallback | None = None,
    ) -> MosaicResult:
        """Rechnet ein gespeichertes Mosaik mit weiteren Änderungen neu (neuer Ordner, neue ID).

        Das alte Mosaik bleibt unverändert. Änderungen sammeln sich: Das neue Mosaik kennt
        alle bisherigen plus ``edits``, kann also selbst wieder bearbeitet werden.

        Args:
            directory: Ordner des Mosaiks (mit ``eingaben.json`` und Originalbild).
            edits: Neue Änderungen.
            required_outputs: Ausgaben, die auf jeden Fall entstehen (z. B. für die API).
            on_step: Fortschritt wie bei ``run``.

        Raises:
            NotFoundError: Wenn Originalbild oder Eingaben fehlen.
            ConfigError: Bei ungültigen Änderungen.
        """
        saved = _read_inputs(directory)
        source = directory / str(saved["bild"])
        if not source.is_file():
            raise NotFoundError(f"Das Originalbild von „{directory.name}“ fehlt.")
        raw = _saved_input(saved)
        overrides = replace(parse_overrides(raw), output_dir=directory.parent)
        if required_outputs:
            outputs = overrides.outputs or self.config.outputs
            missing = tuple(name for name in required_outputs if name not in outputs)
            overrides = replace(overrides, outputs=(*outputs, *missing))
        previous = [edit_from_json(item) for item in _items(saved.get("aenderungen", []))]
        return self.run(
            source.read_bytes(),
            overrides,
            title=str(saved["titel"]),
            on_step=on_step,
            edits=[*previous, *edits],
            inputs=raw,
            base=directory.name,
        )

    def inputs_of(self, directory: Path) -> OverrideInput:
        """Die Eingaben, mit denen ein gespeichertes Mosaik erzeugt wurde.

        Raises:
            NotFoundError: Wenn das Mosaik ohne Eingaben gespeichert wurde.
        """
        return _saved_input(_read_inputs(directory))

    def _build(
        self,
        image: Path | bytes,
        overrides: MosaicOverrides | None,
        title: str,
        timer: _Timer,
        edits: Sequence[GridEdit] = (),
    ) -> _Build:
        """Alle Rechenschritte bis zum fertigen Mosaik (ohne Dateien)."""
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        components = self._component_factory(run_config)
        with timer.step("Teilekatalog laden"):
            catalog = self.load_catalog(run_config)
            palette = _palette(catalog, run_config, components.shaper)
            materials = _Materials.resolve(catalog, run_config, components.shaper)
        with timer.step("Bild laden und einpassen"):
            size = _layout_size(run_config, components.image_preparer, image)
            prepared = components.image_preparer.prepare(
                image,
                width=size.image_width,
                height=size.image_height,
                pad_color=materials.pad_color.rgb,
                background_color=materials.background_rgb,
            )
            detail = _detail_image(components, image, size, materials, run_config.frame.width)
            fine = _fine_image(components, image, size, materials, detail, run_config)
        with timer.step("Dithering"):
            grid, palette = _dither(components, prepared.rgb, fine, palette, run_config)
            fixed = np.zeros(grid.shape, dtype=bool)  # Zellen mit fester Farbe (für die Formwahl)
            if materials.background_color is not None and prepared.background is not None:
                grid, palette = _fill(
                    grid, palette, prepared.background, materials.background_color
                )
                fixed |= prepared.background
            if edits:
                grid, palette, changed = apply_edits(
                    grid,
                    palette,
                    edits,
                    {color.id: color for color in materials.allowed},
                    frame_width=run_config.frame.width,
                )
                fixed |= changed
            detail = _flatten_detail(components, detail, grid, palette, fixed, run_config)
            grid, palette = apply_frame(
                grid,
                palette,
                run_config.frame.width,
                materials.frame_color,
                materials.corner_color,
            )
        with timer.step("Relief und Formteile"):
            height, width = grid.shape
            image_area = ~frame_mask(height, width, run_config.frame.width)
            levels = relief_levels(
                np.where(image_area, grid, EMPTY_CELL).astype(np.intp),
                palette,
                run_config.relief.mode,
                run_config.relief.levels,
                _framed(prepared.background, run_config.frame.width),
            )
            shaped = _shape_sections(
                components,
                grid,
                image_area,
                detail,
                palette,
                catalog,
                levels,
                materials,
                run_config,
            )
            grid = _with_shape_colors(grid, palette, shaped.parts)
        with timer.step("Zerlegung in Teile"):
            layers = _tile_layers(components, grid, palette, catalog, run_config, levels, shaped)
            substrate, connectors = _substrate_layers(materials, size, run_config)
            bond = _bond_layer(components.bond_tiler, materials, size, catalog, run_config)
            if bond:  # alles über der Verbindungslage liegt eine Plate höher
                layers = layers.raised(PLATE_HEIGHT_LDU)
        mosaic = Mosaic(
            title=title,
            width=size.width,
            height=size.height,
            color_grid=grid,
            palette=palette,
            placements=layers.image,
            frame=layers.frame,
            supports=layers.supports,
            relief=layers.relief,
            underlays=layers.underlays,
            substrate=substrate,
            connectors=connectors,
            bond=bond,
            element_ids=catalog.element_ids,
        )
        stats = _stats(size, mosaic, timer.durations, run_config)
        mosaic = replace(
            mosaic, metadata={"konfiguration": config_to_dict(run_config), "statistik": stats}
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
    relief_color: LegoColor | None  # Farbe der Stützlagen unter erhöhten Bildteilen
    bond_color: LegoColor | None  # Farbe der Verbindungslage über großen Platten
    # Farben, die Bearbeiten und Hintergrund einsetzen dürfen (ohne eigene Palette)
    allowed: tuple[LegoColor, ...]
    background_color: LegoColor | None  # neue Farbe des Hintergrunds bei „entfernen“

    @property
    def ground_color(self) -> LegoColor | None:
        """Was in den Lücken der Formteile auf Höhe 0 zu sehen ist (der Unterbau, sonst None)."""
        return self.substrate[1] if self.substrate else None

    @property
    def background_rgb(self) -> RgbTuple | None:
        """RGB der Hintergrundfarbe für die Bildaufbereitung (sonst die Randfarbe)."""
        return None if self.background_color is None else self.background_color.rgb

    @classmethod
    def resolve(cls, catalog: PartCatalog, config: AppConfig, shaper: ShapeRefiner) -> _Materials:
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
        pad_color = _pad_color(catalog, config)
        unrestricted = replace(config, palette=replace(config.palette, only_color_ids=None))
        allowed = _palette(catalog, unrestricted, shaper)
        background_color = _background_color(catalog, config, allowed)
        substrate, connector = _substrate_materials(catalog, config)
        bond_color = None
        if config.layout.substrate is Substrate.PLATES:  # wie der Unterbau, in Plates
            bond_color = require_single_stud_color(
                catalog, substrate_color_id(config.layout), (PartKind.PLATE,), "Farbe des Unterbaus"
            )
        relief_color = None
        if config.relief.mode is not ReliefMode.NONE:
            relief_color = require_single_stud_color(
                catalog, relief_color_id(config), (PartKind.PLATE,), "Farbe der Relief-Stützlagen"
            )
        return cls(
            frame_color,
            corner_color,
            pad_color,
            substrate,
            connector,
            relief_color,
            bond_color,
            allowed,
            background_color,
        )


def _pad_color(catalog: PartCatalog, config: AppConfig) -> LegoColor:
    """Randfarbe beim Einpassen und für Transparenz."""
    pad_color = catalog.colors.get(config.mosaic.pad_color_id)
    if pad_color is None:
        raise ConfigError(f"Die Randfarbe {config.mosaic.pad_color_id} ist unbekannt.")
    return pad_color


def _background_color(
    catalog: PartCatalog, config: AppConfig, allowed: Sequence[LegoColor] | None
) -> LegoColor | None:
    """Neue Hintergrundfarbe bei „entfernen“ (sonst ``None``), gegen ``allowed`` geprüft.

    Ohne Angabe gilt die Randfarbe. ``allowed=None`` prüft nur, dass es die Farbe gibt.
    """
    mosaic = config.mosaic
    if mosaic.background is not BackgroundMode.REMOVE:
        return None
    color_id = (
        mosaic.pad_color_id if mosaic.background_color_id is None else mosaic.background_color_id
    )
    color = catalog.colors.get(color_id)
    if color is None:
        raise ConfigError(f"Die Hintergrundfarbe {color_id} ist unbekannt.")
    if allowed is not None and color not in allowed:
        raise ConfigError(
            f"Die Hintergrundfarbe {color.name} gibt es nicht als 1×1-Teil der gewählten "
            "Teilarten und des Stils."
        )
    return color


def _fill(
    grid: IndexGrid, palette: tuple[LegoColor, ...], mask: BoolGrid, color: LegoColor
) -> tuple[IndexGrid, tuple[LegoColor, ...]]:
    """Setzt alle Zellen der Maske auf eine Farbe (ggf. neu in der Palette)."""
    if color not in palette:
        palette = (*palette, color)
    return np.where(mask, palette.index(color), grid).astype(np.intp), palette


def _flatten_detail(
    components: PipelineComponents,
    detail: FloatImage | None,
    grid: IndexGrid,
    palette: tuple[LegoColor, ...],
    fixed: BoolGrid,
    config: AppConfig,
) -> FloatImage | None:
    """Macht das Detailbild der Formwahl dort einfarbig, wo die Farbe fest vorgegeben ist.

    Sonst würde die Formwahl an umgefärbten Noppen nach dem alten Bild Rundungen setzen.
    """
    if detail is None or not fixed.any():
        return detail
    s = components.shaper.subsample
    border = config.frame.width * s
    lab = components.color_space.to_lab(np.array([c.rgb for c in palette], dtype=np.float64))
    # np.kron vergrößert die Maske: jede Noppe wird zu s × s Teilpixeln
    fine = np.kron(fixed, np.ones((s, s), dtype=bool)).astype(bool)
    colors = lab[np.kron(grid, np.ones((s, s), dtype=np.intp))]
    result = detail.copy()
    inner = result[border : border + fine.shape[0], border : border + fine.shape[1]]
    inner[fine] = colors[fine]
    return result


def _framed(mask: BoolGrid | None, frame_width: int) -> BoolGrid | None:
    """Maske der Bildfläche auf die ganze Mosaikfläche erweitern (Rahmen = kein Hintergrund)."""
    if mask is None:
        return None
    return np.pad(mask, frame_width, constant_values=False)


def _keep_source(
    target_dir: Path,
    image: Path | bytes,
    image_format: str | None,
    inputs: OverrideInput | None,
    title: str,
    edits: Sequence[GridEdit],
    base: str | None,
) -> None:
    """Speichert Originalbild und Eingaben im Mosaik-Ordner (für ``edit`` und Presets)."""
    data = image.read_bytes() if isinstance(image, Path) else image
    suffix = _image_suffix(image_format)
    (target_dir / f"{SOURCE_STEM}{suffix}").write_bytes(data)
    raw = asdict(inputs or OverrideInput())
    raw["output_dir"] = None  # Ordner gehört nicht zu den Einstellungen des Bilds
    saved = {
        "version": INPUTS_VERSION,
        "titel": title,
        "bild": f"{SOURCE_STEM}{suffix}",
        "basis": base,
        "eingaben": raw,
        "aenderungen": [edit_to_json(edit) for edit in edits],
    }
    text = json.dumps(saved, ensure_ascii=False, indent=2) + "\n"
    (target_dir / INPUTS_FILE).write_text(text, encoding="utf-8")


def _image_suffix(image_format: str | None) -> str:
    """Dateiendung nach dem Bildformat laut Pillow (z. B. „JPEG“ → „.jpg“)."""
    if not image_format:
        return ".bild"
    return IMAGE_SUFFIXES.get(image_format, f".{image_format.lower()}")


def _read_inputs(directory: Path) -> dict[str, object]:
    """Inhalt von ``eingaben.json`` eines Mosaiks.

    Raises:
        NotFoundError: Wenn das Mosaik ohne Eingaben gespeichert wurde.
    """
    path = directory / INPUTS_FILE
    if not path.is_file():
        raise NotFoundError(
            f"„{directory.name}“ wurde ohne Originalbild gespeichert und lässt sich nicht "
            "bearbeiten (storage.keep_source)."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path} ist beschädigt.")
    return data


def _saved_input(saved: Mapping[str, object]) -> OverrideInput:
    """Die gespeicherten Eingaben aus ``eingaben.json`` als ``OverrideInput``."""
    raw = saved.get("eingaben")
    if not isinstance(raw, dict):
        raise ConfigError("eingaben.json enthält keine Eingaben.")
    known = {
        name: value for name, value in raw.items() if name in OverrideInput.__dataclass_fields__
    }
    known["output_dir"] = None
    return OverrideInput(**known)


def _items(value: object) -> list[Mapping[str, object]]:
    """Liste von dicts aus JSON (alles andere ist ein Fehler)."""
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ConfigError("Die gespeicherten Änderungen sind beschädigt.")
    return value


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
        # Mit Unterbau nur ganze Platten: Kein Paneel steht über, keins fehlt
        snap = None if layout.substrate is Substrate.NONE else layout.plate_studs
        ratio = 1.0 if width and height else preparer.inspect(image).aspect_ratio
        size_studs = complete_size(width, height, ratio, frame, snap)
        if size_studs != (width, height):
            logger.info("Größe angepasst: %d × %d Noppen.", *size_studs)
        size = custom_layout_size(*size_studs, layout.plate_studs, frame)
    if layout.substrate is Substrate.BASEPLATE and size.plates_wide * size.plates_high > 1:
        logger.warning(
            "Mehrere Grundplatten liegen nur lose nebeneinander. Für ein stabiles Bild "
            "„--unterbau art“ (LEGO-Art-Paneele mit Technic-Pins) verwenden."
        )
    return size


@dataclass(frozen=True, slots=True)
class _Layers:
    """Zerlegte Lagen: Bild (pro Abschnitt), Rahmen, Stützteile, Relief und Unterlagen."""

    image: tuple[Placement, ...]
    frame: tuple[Placement, ...]
    supports: tuple[Placement, ...]
    relief: tuple[Placement, ...] = ()
    underlays: tuple[Placement, ...] = ()

    def raised(self, ldu: int) -> _Layers:
        """Alle Lagen um ``ldu`` angehoben (z. B. über eine Verbindungslage)."""

        def lift(placements: tuple[Placement, ...]) -> tuple[Placement, ...]:
            return tuple(replace(p, bottom_ldu=p.bottom_ldu + ldu) for p in placements)

        return _Layers(
            lift(self.image),
            lift(self.frame),
            lift(self.supports),
            lift(self.relief),
            lift(self.underlays),
        )


def _tile_layers(
    components: PipelineComponents,
    grid: IndexGrid,
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    config: AppConfig,
    levels: IndexGrid,
    shaped: ShapeResult,
) -> _Layers:
    """Zerlegt Bild und Rahmen getrennt; ein erhöhter Rahmen bekommt Stützlagen.

    Der Rahmen wird am Stück zerlegt (seine Teile dürfen Abschnittsgrenzen und
    Plattennähte überbrücken), das Bild pro Abschnitt und pro Relief-Stufe: Ein Teil
    darf nie über eine Stufe reichen. Formteile stehen schon fest und bleiben frei;
    ihre Unterlagen sind eine eigene Lage (sie werden vor dem Bild gebaut).
    """
    height, width = grid.shape
    ring = frame_mask(height, width, config.frame.width)
    section = config.instructions.section_studs
    size = section if config.tiling.split_sections else max(height, width)
    free = ~ring
    for placement in shaped.parts:
        for cell in placement.cells():
            free[cell] = False
    image = list(_lift(shaped.parts, levels))
    for level in np.unique(levels[free]).tolist():
        # np.where(Bedingung, a, b): pro Zelle a, wo die Bedingung gilt, sonst b
        level_grid = np.where(free & (levels == level), grid, EMPTY_CELL).astype(np.intp)
        tiled = _tile_sections(components.tiler, level_grid, palette, catalog, size)
        image += [replace(p, bottom_ldu=level * PLATE_HEIGHT_LDU) for p in tiled]
    relief = _relief_supports(components, levels, ~ring, catalog, config, size)
    underlays = tuple(_lift(shaped.underlays, levels))
    if not ring.any():
        return _Layers(tuple(image), (), (), relief, underlays)
    raise_ldu = config.frame.raise_plates * PLATE_HEIGHT_LDU
    frame_grid = np.where(ring, grid, EMPTY_CELL).astype(np.intp)
    frame = tuple(
        replace(placement, bottom_ldu=raise_ldu)
        for placement in components.frame_tiler.tile(frame_grid, palette, catalog)
    )
    supports = _supports(components.support_tiler, ring, palette, catalog, config)
    return _Layers(tuple(image), frame, supports, relief, underlays)


def _lift(placements: Iterable[Placement], levels: IndexGrid) -> Iterator[Placement]:
    """Hebt Teile um die Relief-Höhe ihrer Startzelle an (ihre eigene Höhe bleibt erhalten)."""
    for placement in placements:
        level = int(levels[placement.row, placement.col])
        yield replace(placement, bottom_ldu=placement.bottom_ldu + level * PLATE_HEIGHT_LDU)


def _relief_supports(
    components: PipelineComponents,
    levels: IndexGrid,
    image_area: npt.NDArray[np.bool_],
    catalog: PartCatalog,
    config: AppConfig,
    section_studs: int,
) -> tuple[Placement, ...]:
    """Stützen unter erhöhten Bildteilen: Unter einer Zelle der Höhe h stehen h Plates.

    Wo mindestens drei Plates übereinander kämen, steht stattdessen ein Brick (so hoch wie
    drei Plates): Das spart zwei Drittel der Teile und Bauschritte. Was übrig bleibt,
    wird mit Plates aufgefüllt. Zerlegt wird pro Abschnitt, damit jede Lage zu genau
    einem Bauabschnitt gehört.
    """
    top = int(levels.max(initial=0))
    if top == 0:
        return ()
    color = catalog.colors[relief_color_id(config)]
    brick_plates = _brick_plates(catalog, color)
    placements: list[Placement] = []

    def add(tiler: TilingStrategy, mask: npt.NDArray[np.bool_], level: int) -> None:
        layer = np.where(mask, 0, EMPTY_CELL).astype(np.intp)
        placements.extend(
            replace(p, bottom_ldu=level * PLATE_HEIGHT_LDU)
            for p in _tile_sections(tiler, layer, (color,), catalog, section_studs)
        )

    level = 0
    while level < top:
        bricks = image_area & (levels >= level + brick_plates) if brick_plates else None
        if bricks is not None and bricks.any():
            add(components.brick_tiler, bricks, level)
        # Plates dort, wo kein Brick steht; ohne Bricks eine Plate pro Lage
        step = brick_plates or 1
        for plate_level in range(level, min(level + step, top)):
            mask = image_area & (levels > plate_level)
            if bricks is not None:
                mask &= ~bricks
            if mask.any():
                add(components.support_tiler, mask, plate_level)
        level += step
    return tuple(placements)


def _brick_plates(catalog: PartCatalog, color: LegoColor) -> int:
    """Höhe eines Bricks in Plates, wenn es Bricks samt 1 × 1 in der Farbe gibt; sonst 0.

    Der 1 × 1 Brick ist nötig, damit sich jede Fläche lückenlos füllen lässt.
    """
    bricks = catalog.parts_in_color(color.id, (PartKind.BRICK,))
    if not any(part.is_single_stud for part in bricks):
        return 0
    return bricks[0].height_ldu // PLATE_HEIGHT_LDU


def _detail_image(
    components: PipelineComponents,
    image: Path | bytes,
    size: LayoutSize,
    materials: _Materials,
    frame_width: int,
) -> FloatImage | None:
    """Bild in ``subsample``-facher Auflösung im Lab-Raum, auf die ganze Mosaikfläche gebracht.

    Nur die Formwahl braucht es; die Rahmenzellen bleiben 0 (sie werden nie bewertet).
    """
    s = components.shaper.subsample
    if s == 0:
        return None
    rgb = components.image_preparer.prepare(
        image,
        width=size.image_width * s,
        height=size.image_height * s,
        pad_color=materials.pad_color.rgb,
        background_color=materials.background_rgb,
    ).rgb
    lab = components.color_space.to_lab(rgb.astype(np.float64))
    border = frame_width * s
    # np.pad: auf allen Seiten ``border`` Zeilen/Spalten mit 0 anhängen (nur Höhe und Breite)
    return np.pad(lab, ((border, border), (border, border), (0, 0)))


def _shape_sections(
    components: PipelineComponents,
    grid: IndexGrid,
    image_area: npt.NDArray[np.bool_],
    detail: FloatImage | None,
    palette: tuple[LegoColor, ...],
    catalog: PartCatalog,
    levels: IndexGrid,
    materials: _Materials,
    config: AppConfig,
) -> ShapeResult:
    """Formwahl pro Bauabschnitt (wie die Zerlegung): Kein Formteil ragt in einen anderen."""
    shaper = components.shaper
    to_lab = components.color_space.to_lab

    def lab(color: LegoColor | None) -> FloatImage:
        """Lab-Wert einer Farbe; ohne Farbe NaN = kein Untergrund (nur mit Unterlage)."""
        if color is None:
            return np.full(3, np.nan)
        result: FloatImage = to_lab(np.array([color.rgb], dtype=np.float64))[0]
        return result

    ground = materials.bond_color or materials.ground_color
    raised = materials.relief_color or ground
    background = np.where((levels > 0)[..., None], lab(raised), lab(ground))
    palette_lab = to_lab(np.array([c.rgb for c in palette], dtype=np.float64))
    masked = np.where(image_area, grid, EMPTY_CELL).astype(np.intp)
    height, width = grid.shape
    s = shaper.subsample
    parts: list[Placement] = []
    underlays: list[Placement] = []
    for section in split_sections(width, height, config.instructions.section_studs):
        rows = slice(section.row, section.row + section.rows)
        cols = slice(section.col, section.col + section.cols)
        sub_detail = None
        if detail is not None:
            sub_detail = detail[rows.start * s : rows.stop * s, cols.start * s : cols.stop * s]
        found = shaper.refine(
            masked[rows, cols],
            sub_detail,
            palette,
            palette_lab,
            catalog,
            levels=levels[rows, cols],
            background_lab=background[rows, cols],
        )
        parts += _moved(found.parts, section.row, section.col)
        underlays += _moved(found.underlays, section.row, section.col)
    return ShapeResult(tuple(parts), tuple(underlays))


def _moved(placements: Iterable[Placement], rows: int, cols: int) -> list[Placement]:
    """Verschiebt Teile aus einem Teilraster an ihre echte Position im Mosaik."""
    return [replace(p, row=p.row + rows, col=p.col + cols) for p in placements]


def _with_shape_colors(
    grid: IndexGrid, palette: tuple[LegoColor, ...], shaped: Sequence[Placement]
) -> IndexGrid:
    """Raster mit den Farben der Formteile (die Formwahl darf die Farbe ändern)."""
    if not shaped:
        return grid
    result = grid.copy()
    for placement in shaped:
        for cell in placement.cells():
            result[cell] = palette.index(placement.color)
    return result


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


def _bond_layer(
    tiler: TilingStrategy,
    materials: _Materials,
    size: LayoutSize,
    catalog: PartCatalog,
    config: AppConfig,
) -> tuple[Placement, ...]:
    """Verbindungslage über mehreren großen Platten: Plates „auf Lücke“ über allen Nähten.

    Wie beim Rahmen-Unterbau: Die Lage wird in Abschnitte der Plattengröße zerlegt, die
    gegen die Nähte versetzt sind (``running_bond_offset``). Weil kein Teil eine
    Abschnittsgrenze überquert, liegt jede Naht mitten unter Plates. Mit Plates 16 × 16
    klappt das sofort; sonst werden weitere Versätze probiert, bis jede Naht in jeder
    Zeile und Spalte überbrückt ist.
    """
    color = materials.bond_color
    if color is None or size.plates_wide * size.plates_high < 2:
        return ()
    seam = config.layout.plate_studs
    longest = max(
        (p.length_studs for p in catalog.parts_in_color(color.id, (PartKind.PLATE,))),
        default=1,
    )
    first = running_bond_offset(seam, min(longest, BOND_PART_STUDS))
    # Mit Unterbau ist die Größe immer ein Vielfaches der Platte: die Lage deckt alles ab
    layer = np.zeros((size.height, size.width), dtype=np.intp)  # überall Farbe 0
    attempts = [first, *(o for o in range(1, seam) if o != first)][:MAX_BOND_ATTEMPTS]
    for offset in attempts:
        bond = _tile_sections(tiler, layer, (color,), catalog, seam, offset=offset)
        if _bridges_all_seams(bond, seam, size):
            return bond
    logger.warning("Die Verbindungslage überbrückt nicht jede Naht; bitte Plates 16 × 16 nutzen.")
    return bond


def _bridges_all_seams(bond: Sequence[Placement], seam: int, size: LayoutSize) -> bool:
    """True, wenn an jeder Naht in jeder Zeile und Spalte eine Plate beide Seiten bedeckt."""
    owner = np.full((size.height, size.width), -1)
    for index, placement in enumerate(bond):
        owner[
            placement.row : placement.row + placement.rows_spanned,
            placement.col : placement.col + placement.cols_spanned,
        ] = index
    cols = range(seam, size.width, seam)
    rows = range(seam, size.height, seam)
    across_cols = all((owner[:, col - 1] == owner[:, col]).all() for col in cols)
    return across_cols and all((owner[row - 1, :] == owner[row, :]).all() for row in rows)


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


def _palette(
    catalog: PartCatalog, config: AppConfig, shaper: ShapeRefiner
) -> tuple[LegoColor, ...]:
    """Palette laut Konfiguration; leer ist ein Konfigurationsfehler.

    Der Stil (``shaper``) kann Farben ausschließen, z. B. „punkte“ alle ohne rundes Tile.
    """
    palette = build_palette(
        catalog,
        config.palette.part_kinds,
        categories=config.palette.color_categories,
        include_color_ids=config.palette.include_color_ids,
        exclude_color_ids=config.palette.exclude_color_ids,
        only_color_ids=config.palette.only_color_ids,
    )
    palette = tuple(color for color in palette if shaper.accepts(color, catalog))
    if not palette:
        raise ConfigError(
            "Mit den gewählten Teilarten und Farbkategorien bleibt keine Farbe übrig."
        )
    if config.palette.only_color_ids is not None:
        missing = sorted(set(config.palette.only_color_ids) - {color.id for color in palette})
        if missing:  # nicht schlimm: Die Palette funktioniert mit den übrigen Farben
            logger.warning(
                "Farben %s der eigenen Palette gibt es nicht als 1×1-Teil für Teilarten und "
                "Stil; sie fehlen im Bild.",
                ", ".join(map(str, missing)),
            )
    return palette


MAX_RARE_COLOR_PASSES = 4  # so oft wird höchstens ohne seltene Farben neu gedithert
MAJORITY_SUBSAMPLE = 4  # Teilpixel pro Noppe und Richtung für die Mehrheitsfarbe


def _fine_image(
    components: PipelineComponents,
    image: Path | bytes,
    size: LayoutSize,
    materials: _Materials,
    detail: FloatImage | None,
    config: AppConfig,
) -> FloatImage | None:
    """Feines Bild (Lab) ohne Rahmen für die Mehrheitsfarbe je Noppe; nur ab Glättung MEDIUM.

    Gibt es schon ein Detailbild für die Formwahl, wird es wiederverwendet.
    """
    if not SMOOTHING_LEVELS[config.dithering.smoothing].majority:
        return None
    if detail is not None:
        border = config.frame.width * components.shaper.subsample
        height, width = detail.shape[0] - 2 * border, detail.shape[1] - 2 * border
        return detail[border : border + height, border : border + width]
    rgb = components.image_preparer.prepare(
        image,
        width=size.image_width * MAJORITY_SUBSAMPLE,
        height=size.image_height * MAJORITY_SUBSAMPLE,
        pad_color=materials.pad_color.rgb,
        background_color=materials.background_rgb,
    ).rgb
    return components.color_space.to_lab(rgb.astype(np.float64))


def _dither(
    components: PipelineComponents,
    rgb: RgbImage,
    fine: FloatImage | None,
    palette: tuple[LegoColor, ...],
    config: AppConfig,
) -> tuple[IndexGrid, tuple[LegoColor, ...]]:
    """Wählt die Farbe jeder Noppe und räumt auf.

    Die Farbe kommt aus dem Dithering oder, mit feinem Bild (Glättung ab MEDIUM), aus der
    Mehrheit der Teilpixel einer Noppe. Danach:

    1. Mit ``max_colors``: ein zweites Mal mit den häufigsten Farben.
    2. Mit ``min_color_share``: Farben auf zu wenigen Zellen fallen weg, das Bild wird ohne
       sie neu gedithert (mehrmals, weil dabei neue seltene Farben entstehen können).
    3. Glättung: kleine Flecken ähnlicher Farbe übernehmen die Umgebung, bei STRONG auch
       Zellen mit klarer 3×3-Mehrheit. Kontrastreiche Details (Augen) bleiben.
    4. Mit ``despeckle``: einzelne Pixel bekommen die Farbe ihrer Nachbarn.
    """
    image_lab = components.color_space.to_lab(rgb.astype(np.float64))
    height, width = rgb.shape[:2]

    def lab_of(colors: tuple[LegoColor, ...]) -> FloatImage:
        result: FloatImage = components.color_space.to_lab(
            np.array([c.rgb for c in colors], dtype=np.float64)
        )
        return result

    def dither_with(colors: tuple[LegoColor, ...]) -> IndexGrid:
        if fine is None:
            return components.ditherer.dither(image_lab, lab_of(colors))
        sub = components.color_space.nearest(fine, lab_of(colors))
        return majority_colors(sub, fine.shape[0] // height, len(colors))[:height, :width]

    grid = dither_with(palette)
    max_colors = config.palette.max_colors
    if max_colors is not None and len(palette) > max_colors:
        palette = most_used_colors(grid, palette, max_colors)
        logger.info("Palette auf die %d häufigsten Farben begrenzt.", len(palette))
        grid = dither_with(palette)
    min_cells = math.ceil(config.palette.min_color_share * grid.size)
    for _ in range(MAX_RARE_COLOR_PASSES if min_cells > 1 else 0):
        rare = set(rare_colors(grid, len(palette), min_cells))
        kept = tuple(color for index, color in enumerate(palette) if index not in rare)
        if not rare or not kept:
            break
        logger.info("%d seltene Farben zusammengelegt.", len(palette) - len(kept))
        palette = kept
        grid = dither_with(palette)
    grid = _smooth(components, grid, lab_of(palette), config)
    if config.dithering.despeckle:
        grid = remove_isolated(grid, lab_of(palette))
    return grid, palette


def _smooth(
    components: PipelineComponents, grid: IndexGrid, palette_lab: FloatImage, config: AppConfig
) -> IndexGrid:
    """Glättung laut Stufe: kleine ähnliche Flecken zusammenlegen, ggf. 3×3-Mehrheit."""
    level = SMOOTHING_LEVELS[config.dithering.smoothing]
    if level.min_cells <= 1 and not level.neighborhood:
        return grid
    distances = components.color_space.distances(palette_lab, palette_lab)
    grid = merge_small_regions(grid, distances, level.min_cells, level.max_delta_e)
    if level.neighborhood:
        grid = majority_filter(grid, distances, level.neighborhood, level.max_delta_e)
    return grid


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
    size: LayoutSize, mosaic: Mosaic, durations: Mapping[str, float], config: AppConfig
) -> dict[str, object]:
    """Kennzahlen des Laufs als JSON-fähiges dict (zum Vergleichen von Läufen).

    Alle Zählungen kommen aus dem fertigen Mosaik, damit Titelblatt, Teileliste und
    ``ergebnis.json`` dieselben Zahlen zeigen.
    """
    tiling = TilingStats.from_placements(size.width, size.height, mosaic.surface)
    all_parts = mosaic.all_parts
    single = sum(1 for p in all_parts if p.part.area == 1 and not p.part.is_shaped)
    return {
        "breite": size.width,
        "hoehe": size.height,
        "bild_breite": size.image_width,
        "bild_hoehe": size.image_height,
        "teile": len(all_parts),
        "farben": len(mosaic.part_colors()),
        "altfarben": len(_retired_colors(mosaic, config)),
        "anteil_1x1": round(single / len(all_parts), 3) if all_parts else 0.0,
        **asdict(tiling),  # pixels, parts, savings_percent
        "stuetzteile": len(mosaic.supports),
        "formteile": sum(1 for placement in mosaic.placements if placement.part.is_shaped),
        "relief_teile": len(mosaic.relief),
        "unterlagen": len(mosaic.underlays),
        "unterbau_platten": len(mosaic.substrate),
        "verbinder": len(mosaic.connectors),
        "verbindungsteile": len(mosaic.bond),
        "dauer_sekunden": dict(durations),
    }


def _retired_colors(mosaic: Mosaic, config: AppConfig) -> list[LegoColor]:
    """Farben, die zuletzt vor dem Zeitraum ``recent_years`` in einem Set vorkamen."""
    years = config.source.recent_years
    if years is None:
        return []
    min_year = date.today().year - years + 1
    return [
        color
        for color in mosaic.part_colors()
        if color.last_year is not None and color.last_year < min_year
    ]


def _write_outputs(
    writers: Sequence[OutputWriter], mosaic: Mosaic, target_dir: Path
) -> dict[str, Path]:
    """Ruft alle Writer auf; ``result_json`` zuletzt, damit es alle Dateien sieht."""
    # sorted ist stabil (übrige Reihenfolge bleibt);
    # der Schlüssel False < True schiebt den Final-Writer nach hinten
    ordered = sorted(writers, key=lambda writer: writer.name == FINAL_WRITER_NAME)
    return {writer.name: writer.write(mosaic, target_dir) for writer in ordered}


def safe_name(title: str) -> str:
    """Macht aus einem Titel einen sicheren Ordnernamen ("Katze & Hund" → "Katze_Hund").

    Umlaute werden umschrieben („Über“ → „Ueber“), damit der Name lesbar bleibt.
    """
    cleaned = UNSAFE_NAME_CHARS.sub("_", title.translate(UMLAUTS)).strip("_")
    return cleaned or DEFAULT_TITLE
