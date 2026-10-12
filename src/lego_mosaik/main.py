"""Composition Root: einzige Stelle, an der konfiguriert und verdrahtet wird.

Wer etwas umstellen will (Größe, Teilarten, Farben, Verfahren, Ausgaben), ändert
nur ``CONFIG``. CLI-Optionen und API-Felder überschreiben einzelne Werte nur für
einen Lauf. Nur dieses Modul kennt die konkreten Klassen und übersetzt die
Konfiguration in deren Konstruktorparameter (Dependency Injection).
"""

import functools
import logging
from pathlib import Path

lazy import httpx
lazy import uvicorn

from lego_mosaik.config import (
    AppConfig,
    DitheringConfig,
    InstructionsConfig,
    LayoutConfig,
    LdrawConfig,
    MosaicConfig,
    PaletteConfig,
    PreviewConfig,
    ServerConfig,
    SourceConfig,
    TilingConfig,
    validate_config,
)
from lego_mosaik.domain.models import ColorCategory, ColorDistance, FitMode, PartKind, Resampling
from lego_mosaik.registry import (
    DITHERERS,
    OUTPUT_WRITERS,
    TILING_STRATEGIES,
    resolve,
    validate_registry_names,
)
lazy from lego_mosaik.imaging.color_space import LabColorSpace
lazy from lego_mosaik.imaging.preparer import PillowImagePreparer
lazy from lego_mosaik.interfaces.api import create_app
lazy from lego_mosaik.interfaces.cli import create_cli
lazy from lego_mosaik.pipeline import MosaicPipeline, PipelineComponents
lazy from lego_mosaik.sources.rebrickable_csv import RebrickableCsvSource
lazy from lego_mosaik.sources.updater import CatalogUpdater

CONFIG = AppConfig(
    layout=LayoutConfig(
        baseplate_studs=48,  # Grundplattengröße: 16, 32 oder 48 Noppen
        plates_wide=1,  # Grundplatten nebeneinander
        plates_high=1,  # Grundplatten untereinander → 48 × 48 Noppen
        include_baseplate=True,  # Grundplatten in LDraw-Datei und Teilelisten
        baseplate_color_id=71,  # Light Bluish Gray (Rebrickable-ID)
        frame_width=1,  # Rahmen in Noppen (0 = ohne Rahmen)
        frame_color_id=0,  # Black
    ),
    mosaic=MosaicConfig(
        fit_mode=FitMode.CROP,  # CROP = mittig zuschneiden, PAD = einpassen mit Rand
        pad_color_id=15,  # Randfarbe bei PAD und für Transparenz (15 = White)
        resampling=Resampling.BOX,  # mittelt Flächen, gut für starkes Verkleinern
        contrast=1.0,  # 1.0 = unverändert
        saturation=1.0,  # 1.0 = unverändert
    ),
    palette=PaletteConfig(
        part_kinds=(PartKind.PLATE, PartKind.TILE),  # erlaubt: PLATE, TILE oder nur BRICK
        color_categories=(ColorCategory.SOLID,),  # exotische: METALLIC, PEARL, CHROME, …
        include_color_ids=(),  # einzelne Farben trotz Kategorie erlauben
        exclude_color_ids=(),  # Farben, die nie verwendet werden
        max_colors=None,  # optional auf N häufigste Farben begrenzen
    ),
    dithering=DitheringConfig(
        method="floyd_steinberg",  # Schlüssel aus der Registry (lego-mosaik verfahren)
        strength=1.0,  # 0.0 = kein Fehlertransport, 1.0 = voll
        color_distance=ColorDistance.CIEDE2000,  # oder CIE76 (schneller)
        serpentine=True,  # Zeilen abwechselnd links/rechts abarbeiten
        bayer_size=4,  # nur "ordered": 2, 4 oder 8
        spread=32.0,  # nur "ordered": Stärke in RGB-Stufen
    ),
    tiling=TilingConfig(strategy="greedy", max_part_length=8, split_sections=True),
    outputs=(
        "preview_png",
        "ldraw",
        "parts_list_csv",
        "official_parts_lists",
        "instructions_png",
        "result_json",
    ),
    preview=PreviewConfig(stud_px=16, draw_part_edges=True),
    ldraw=LdrawConfig(rows_per_step=4),
    instructions=InstructionsConfig(
        section_studs=16,  # Bauabschnitte 16 × 16 wie bei LEGO Art
        view_elevation_deg=60.0,  # Kamera: Höhenwinkel der 3D-Ansichten
        view_azimuth_deg=0.0,  # Kamera: 0 = gerade von vorne (Zeilen bleiben abzählbar)
        step_render_height_px=820,  # Größe der Schrittansicht
    ),
    source=SourceConfig(data_dir=Path("daten/rebrickable"), max_age_days=7, auto_update=True),
    output_dir=Path("ausgabe"),
    server=ServerConfig(host="127.0.0.1", port=8000, max_upload_mb=20, update_interval_hours=24),
)


@functools.cache
def build_catalog_source(source: SourceConfig) -> RebrickableCsvSource:
    """Erzeugt die CSV-Datenquelle; ``functools.cache`` liefert pro Konfiguration
    immer dieselbe Instanz, damit der geparste Katalog im Prozess gecacht bleibt."""
    return RebrickableCsvSource(
        data_dir=source.data_dir,
        geometry_file=source.geometry_file,
        color_mapping_file=source.color_mapping_file,
        studio_color_file=source.studio_color_file,
        category_keywords=source.category_keywords,
    )


def build_updater(
    source: SourceConfig, transport: httpx.BaseTransport | None = None
) -> CatalogUpdater:
    """Erzeugt den Updater aus der Konfiguration (``transport`` nur für Tests)."""
    return CatalogUpdater(
        data_dir=source.data_dir,
        base_url=source.base_url,
        file_names=source.files,
        max_age_days=source.max_age_days,
        timeout_seconds=source.timeout_seconds,
        user_agent=source.user_agent,
        transport=transport,
    )


def build_components(config: AppConfig) -> PipelineComponents:
    """Steckt die konkreten Bausteine eines Laufs anhand der Konfiguration zusammen."""
    return PipelineComponents(
        image_preparer=PillowImagePreparer(
            fit_mode=config.mosaic.fit_mode,
            resampling=config.mosaic.resampling,
            contrast=config.mosaic.contrast,
            saturation=config.mosaic.saturation,
        ),
        color_space=LabColorSpace(config.dithering.color_distance),
        ditherer=resolve(DITHERERS, config.dithering.method, "Dithering-Verfahren")(config),
        tiler=resolve(TILING_STRATEGIES, config.tiling.strategy, "Zerlegungsverfahren")(config),
        writers=tuple(
            resolve(OUTPUT_WRITERS, name, "Ausgabeformat")(config) for name in config.outputs
        ),
    )


def build_pipeline(config: AppConfig) -> MosaicPipeline:
    """Prüft die Konfiguration und baut die Pipeline mit allen Abhängigkeiten.

    Raises:
        ConfigError: Bei ungültigen Werten oder unbekannten Registry-Namen.
    """
    validate_config(config)
    validate_registry_names(config)
    return MosaicPipeline(
        config,
        catalog_source=build_catalog_source(config.source),
        updater=build_updater(config.source),
        component_factory=build_components,
        method_names={
            "Dithering": tuple(DITHERERS),
            "Zerlegung": tuple(TILING_STRATEGIES),
            "Ausgaben": tuple(OUTPUT_WRITERS),
        },
    )


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
logger = logging.getLogger(__name__)


def serve(config: AppConfig, host: str, port: int) -> None:
    """Startet die FastAPI-App mit uvicorn (Befehl ``lego-mosaik server``)."""
    if host not in LOOPBACK_HOSTS:
        logger.warning("Achtung: %s ist von außen erreichbar und hat keine Anmeldung!", host)
    uvicorn.run(create_app(config, build_pipeline), host=host, port=port)


def run() -> None:
    """Entry Point aus pyproject.toml: startet die deutsche CLI."""
    create_cli(CONFIG, build_pipeline, serve)()
