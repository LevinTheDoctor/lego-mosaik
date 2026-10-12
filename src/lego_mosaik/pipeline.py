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
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

lazy import numpy as np

from lego_mosaik.config import AppConfig, MosaicOverrides, apply_overrides, config_to_dict
from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.layout import (
    LayoutSize,
    apply_frame,
    baseplate_placements,
    find_baseplate,
    layout_size,
    require_baseplate_color,
    require_frame_color,
)
from lego_mosaik.domain.models import (
    IndexGrid,
    LegoColor,
    Mosaic,
    PartCatalog,
    Placement,
    TilingStats,
)
from lego_mosaik.domain.palette import build_palette, most_used_colors
lazy from lego_mosaik.dithering.protocol import ColorSpace, Ditherer
lazy from lego_mosaik.imaging.protocol import ImagePreparer, RgbImage
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
    """Die austauschbaren Bausteine eines Laufs (von ``main.py`` gebaut)."""

    image_preparer: ImagePreparer
    color_space: ColorSpace
    ditherer: Ditherer
    tiler: TilingStrategy
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

    # --- Mosaik erzeugen -------------------------------------------------------

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
        run_config = apply_overrides(self.config, overrides or MosaicOverrides())
        components = self._component_factory(run_config)
        timer = _Timer(on_step)
        with timer.step("Teilekatalog laden"):
            catalog = self.load_catalog(run_config)
            palette = _palette(catalog, run_config)
            layout = _Layout.resolve(catalog, run_config)
        with timer.step("Bild laden und einpassen"):
            rgb = components.image_preparer.prepare(
                image,
                width=layout.size.image_width,
                height=layout.size.image_height,
                pad_color=layout.pad_color.rgb,
            )
        with timer.step("Dithering"):
            grid, palette = _dither(components, rgb, palette, run_config.palette.max_colors)
            grid, palette = apply_frame(
                grid, palette, run_config.layout.frame_width, layout.frame_color
            )
        with timer.step("Zerlegung in Teile"):
            placements = tuple(components.tiler.tile(grid, palette, catalog))
        stats = _stats(layout.size, placements, timer.durations)
        mosaic = Mosaic(
            title=title,
            width=layout.size.width,
            height=layout.size.height,
            color_grid=grid,
            palette=palette,
            placements=placements,
            baseplates=layout.baseplates,
            element_ids=catalog.element_ids,
            metadata={"konfiguration": config_to_dict(run_config), "statistik": stats},
        )
        target_dir = self._create_target_dir(run_config.output_dir, title)
        with timer.step("Ausgaben schreiben"):
            files = _write_outputs(components.writers, mosaic, target_dir)
        stats["dauer_sekunden"] = dict(timer.durations)
        return MosaicResult(target_dir.name, mosaic, target_dir, files, stats)

    def _create_target_dir(self, output_dir: Path, title: str) -> Path:
        """Legt ``<ausgabe>/<zeitstempel>_<titel>`` an; bei Kollision mit Zähler."""
        base = f"{self._now().strftime(TIMESTAMP_FORMAT)}_{safe_name(title)}"
        candidate, counter = output_dir / base, 2
        while candidate.exists():
            candidate, counter = output_dir / f"{base}_{counter}", counter + 1
        candidate.mkdir(parents=True)
        return candidate


@dataclass(frozen=True, slots=True)
class _Layout:
    """Aufgelöstes Layout eines Laufs (Farben und Grundplatten aus dem Katalog)."""

    size: LayoutSize
    frame_color: LegoColor
    pad_color: LegoColor
    baseplates: tuple[Placement, ...]

    @classmethod
    def resolve(cls, catalog: PartCatalog, config: AppConfig) -> _Layout:
        """Prüft Farben und Grundplatte gegen den Katalog (deutsche Fehlermeldungen)."""
        layout = config.layout
        size = layout_size(
            layout.baseplate_studs, layout.plates_wide, layout.plates_high, layout.frame_width
        )
        frame_color = require_frame_color(catalog, layout.frame_color_id, config.palette.part_kinds)
        pad_color = catalog.colors.get(config.mosaic.pad_color_id)
        if pad_color is None:
            raise ConfigError(f"Die Randfarbe {config.mosaic.pad_color_id} ist unbekannt.")
        baseplates: tuple[Placement, ...] = ()
        if layout.include_baseplate:
            baseplate = find_baseplate(catalog, layout.baseplate_studs)
            color = require_baseplate_color(catalog, baseplate, layout.baseplate_color_id)
            baseplates = baseplate_placements(
                baseplate, color, layout.plates_wide, layout.plates_high
            )
        return cls(size, frame_color, pad_color, baseplates)


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


def _stats(
    size: LayoutSize, placements: Sequence[Placement], durations: Mapping[str, float]
) -> dict[str, object]:
    """Kennzahlen des Laufs als JSON-fähiges dict."""
    tiling = TilingStats.from_placements(size.width, size.height, placements)
    return {
        "breite": size.width,
        "hoehe": size.height,
        "bild_breite": size.image_width,
        "bild_hoehe": size.image_height,
        "farben": len({placement.color.id for placement in placements}),
        **asdict(tiling),  # pixels, parts, savings_percent
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
