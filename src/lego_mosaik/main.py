"""Composition Root: einzige Stelle, an der konfiguriert und verdrahtet wird.

Wer etwas umstellen will (Größe, Teilarten, Farben, Verfahren, Ausgaben), ändert
nur ``CONFIG``. CLI-Optionen und API-Felder überschreiben einzelne Werte nur für
einen Lauf. Nur dieses Modul kennt die konkreten Klassen und übersetzt die
Konfiguration in deren Konstruktorparameter (Dependency Injection).
"""

import functools
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

lazy import httpx
lazy import uvicorn

from lego_mosaik.config import (
    AppConfig,
    DitheringConfig,
    FrameConfig,
    InstructionsConfig,
    LayoutConfig,
    LdrawConfig,
    MosaicConfig,
    PaletteConfig,
    PreviewConfig,
    PricingConfig,
    ReliefConfig,
    ServerConfig,
    ShapeConfig,
    SourceConfig,
    StorageConfig,
    TilingConfig,
    frame_part_kinds,
    validate_config,
)
from lego_mosaik.domain.layout import BOND_PART_STUDS
from lego_mosaik.domain.models import (
    BackgroundMode,
    ColorCategory,
    ColorDistance,
    ColorFilter,
    FitMode,
    PartKind,
    ReliefMode,
    Resampling,
    Smoothing,
    Substrate,
)
from lego_mosaik.registry import (
    DITHERERS,
    OUTPUT_WRITERS,
    SHAPERS,
    TILING_STRATEGIES,
    resolve,
    validate_registry_names,
)
lazy from lego_mosaik.imaging.color_space import LabColorSpace
lazy from lego_mosaik.imaging.preparer import ImageEdits, PillowImagePreparer
lazy from lego_mosaik.interfaces.api import create_app
lazy from lego_mosaik.interfaces.cli import create_cli
lazy from lego_mosaik.pipeline import MosaicPipeline, MosaicResult, PipelineComponents
lazy from lego_mosaik.pricing.bricklink_api import BrickLinkPriceSource
lazy from lego_mosaik.pricing.cache import PriceCache, price_cache_key
lazy from lego_mosaik.pricing.credentials import load_credentials
lazy from lego_mosaik.pricing.protocol import PriceCacheProtocol
lazy from lego_mosaik.pricing.service import PriceService
lazy from lego_mosaik.sources.protocol import CatalogUpdaterProtocol, PartCatalogSource
lazy from lego_mosaik.sources.rebrickable_csv import RebrickableCsvSource
lazy from lego_mosaik.sources.updater import CatalogUpdater
lazy from lego_mosaik.tiling.protocol import TilingStrategy

CONFIG = AppConfig(
    layout=LayoutConfig(
        substrate=Substrate.ART_PANEL,  # ART_PANEL (Pins), PLATES (große Platten), BASEPLATE, NONE
        plate_studs=16,  # Art-Paneel 16; große Platten und Grundplatten 16, 32 oder 48 Noppen
        plates_wide=3,  # Platten nebeneinander
        plates_high=3,  # Platten untereinander → 48 × 48 Noppen
        width_studs=None,  # freie Größe in Noppen statt Platten (None = aus den Platten)
        height_studs=None,  # fehlt eine Seite, kommt sie aus dem Seitenverhältnis
        substrate_color_id=None,  # None = Standard (Art: Black, Grundplatte: Light Bluish Gray)
        pins_per_seam=2,  # Technic-Pins pro Naht zwischen zwei Art-Paneelen
    ),
    frame=FrameConfig(
        width=1,  # Rahmen in Noppen (0 = ohne Rahmen)
        color_id=0,  # Black
        corner_color_id=None,  # eigene Farbe für die Ecken, z. B. 15 (White)
        part_kinds=(PartKind.TILE,),  # glatter Rahmen wie bei LEGO Art; None = wie das Bild
        raise_plates=1,  # Rahmen eine Plate höher als das Bild (0 = bündig)
    ),
    mosaic=MosaicConfig(
        fit_mode=FitMode.CROP,  # CROP = mittig zuschneiden, PAD = einpassen mit Rand
        pad_color_id=15,  # Randfarbe bei PAD und für Transparenz (15 = White)
        resampling=Resampling.BOX,  # mittelt Flächen, gut für starkes Verkleinern
        contrast=1.0,  # 1.0 = unverändert
        saturation=1.0,  # 1.0 = unverändert
        brightness=1.0,  # 1.0 = unverändert
        color_filter=ColorFilter.NONE,  # GRAYSCALE, SEPIA, WARM, COOL, INVERT, POSTERIZE, VINTAGE
        filter_strength=1.0,  # 0.0 = Original, 1.0 = voller Filter
        rotate=0,  # 0, 90, 180, 270 Grad im Uhrzeigersinn
        mirror=False,  # waagerecht spiegeln
        crop=None,  # Ausschnitt (x, y, Breite, Höhe) in Prozent, z. B. (10, 0, 80, 100)
        background=BackgroundMode.KEEP,  # REMOVE, LIGHTEN, DARKEN, DESATURATE, BLUR
        background_color_id=None,  # neue Farbe bei REMOVE; None = Randfarbe (White)
        background_tolerance=12.0,  # ΔE: so ähnlich muss ein Pixel der Randfarbe sein
        background_amount=0.5,  # Stärke für aufhellen, abdunkeln, entsättigen, weichzeichnen
    ),
    palette=PaletteConfig(
        part_kinds=(PartKind.PLATE, PartKind.TILE),  # erlaubt: PLATE, TILE oder nur BRICK
        color_categories=(ColorCategory.SOLID,),  # exotische: METALLIC, PEARL, CHROME, …
        include_color_ids=(),  # einzelne Farben trotz Kategorie erlauben
        exclude_color_ids=(),  # Farben, die nie verwendet werden
        only_color_ids=None,  # eigene Palette: nur diese Farb-IDs (None = alle)
        max_colors=None,  # optional auf N häufigste Farben begrenzen
        min_color_share=0.002,  # Farben auf weniger als 0,2 % der Fläche zusammenlegen
    ),
    dithering=DitheringConfig(
        method="floyd_steinberg",  # Schlüssel aus der Registry (lego-mosaik verfahren)
        strength=1.0,  # 0.0 = kein Fehlertransport, 1.0 = voll
        color_distance=ColorDistance.CIEDE2000,  # oder CIE76 (schneller)
        serpentine=True,  # Zeilen abwechselnd links/rechts abarbeiten
        bayer_size=4,  # nur "ordered": 2, 4 oder 8
        spread=32.0,  # nur "ordered": Stärke in RGB-Stufen
        despeckle=False,  # einzelne Pixel entfernen (gut für Comics, --aufraeumen)
        smoothing=Smoothing.LIGHT,  # aus, leicht, mittel (Mehrheitsfarbe je Noppe), stark
    ),
    shapes=ShapeConfig(
        style="formen",  # formen (Rundungen, Wedges an Kanten), quadrat, punkte, noppen
        subsample=6,  # „formen“: Teilpixel pro Noppe und Richtung
        min_gain=0.2,  # „formen“: Formteil nur bei mindestens 20 % weniger Fehler …
        min_improvement=3.0,  # … und mindestens ΔE 3 Verbesserung pro Teilpixel
        underlay=True,  # „formen“: Formteile auf Plates in zweiter Farbe (wie Brick Sketches)
    ),
    relief=ReliefConfig(
        mode=ReliefMode.NONE,  # NONE, SUBJECT (Motiv erhöht), BRIGHT, DARK
        levels=1,  # höchste Stufe in Plates (1–3)
        color_id=None,  # Farbe der Stützlagen; None = wie der Unterbau
    ),
    tiling=TilingConfig(strategy="greedy", max_part_length=8, split_sections=True),
    outputs=(
        "preview_png",
        "ldraw",
        "parts_list_csv",
        "official_parts_lists",
        "instructions_png",
        "raster_json",
        "result_json",
    ),
    preview=PreviewConfig(stud_px=16, draw_part_edges=True),
    ldraw=LdrawConfig(rows_per_step=4),
    instructions=InstructionsConfig(
        section_studs=16,  # Bauabschnitte 16 × 16 wie bei LEGO Art
        view_elevation_deg=60.0,  # Kamera: Höhenwinkel der 3D-Ansichten
        view_azimuth_deg=0.0,  # Kamera: 0 = gerade von vorne (Zeilen bleiben abzählbar)
        step_render_height_px=900,  # höchstens so hoch ist eine Schrittansicht
    ),
    source=SourceConfig(
        data_dir=Path("daten/rebrickable"),
        max_age_days=7,
        auto_update=True,
        recent_years=3,  # nur Teile in Farben aus Sets der letzten 3 Jahre (None = alle)
    ),
    output_dir=Path("ausgabe"),
    server=ServerConfig(host="127.0.0.1", port=8000, max_upload_mb=20, update_interval_hours=24),
    pricing=PricingConfig(
        currency="EUR",  # Währung der BrickLink-Preise
        region="europe",  # Verkäufer aus Europa (None = weltweit)
        country_code=None,  # oder nur aus einem Land, z. B. "DE"
        condition="N",  # N = neu, U = gebraucht (auch in der Wanted List)
        vat="Y",  # Preise mit MwSt.
        max_age_days=7,  # gespeicherte Preise so lange verwenden
        max_price_factor=None,  # z. B. 1.5 = Höchstpreis in der Wanted List (Ø verkauft × 1,5)
    ),
    storage=StorageConfig(
        palettes_file=Path("daten/paletten.json"),  # eigene Farbpaletten
        presets_file=Path("daten/presets.json"),  # Presets mit Bewertung und Favoriten
        keep_source=True,  # Originalbild + eingaben.json im Mosaik (nötig für „bearbeiten“)
    ),
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
        recent_years=source.recent_years,
    )


def build_updater(
    source: SourceConfig, transport: httpx.BaseTransport | None = None
) -> CatalogUpdater:
    """Erzeugt den Updater aus der Konfiguration (``transport`` nur für Tests)."""
    return CatalogUpdater(
        data_dir=source.data_dir,
        base_url=source.base_url,
        file_names=source.files,
        optional_file_names=source.optional_files,
        max_age_days=source.max_age_days,
        timeout_seconds=source.timeout_seconds,
        user_agent=source.user_agent,
        transport=transport,
    )


def build_price_service(
    config: AppConfig,
    transport: httpx.BaseTransport | None = None,
    *,
    cache: PriceCacheProtocol | None = None,
) -> PriceService:
    """Preisschätzung über BrickLink (``transport`` nur für Tests).

    Die Zugangsdaten werden erst gelesen, wenn wirklich abgefragt wird; ``--offline``
    funktioniert deshalb auch ohne sie. Ohne ``cache`` liegen die Preise in
    ``pricing.cache_file`` (die Cloud-Variante übergibt einen PostgreSQL-Cache).
    """
    pricing = config.pricing

    def source() -> BrickLinkPriceSource:
        return BrickLinkPriceSource(
            credentials=load_credentials(),
            base_url=pricing.base_url,
            currency=pricing.currency,
            region=pricing.region,
            country_code=pricing.country_code,
            condition=pricing.condition,
            vat=pricing.vat,
            timeout_seconds=pricing.timeout_seconds,
            max_parallel=pricing.max_parallel,
            user_agent=config.source.user_agent,
            transport=transport,
        )

    validate_config(config)
    return PriceService(
        pricing,
        output_dir=config.output_dir,
        cache=cache
        or PriceCache(pricing.cache_file, price_cache_key(pricing), pricing.max_age_days),
        source_factory=source,
    )


def build_tiler(
    config: AppConfig, part_kinds: tuple[PartKind, ...], max_part_length: int | None = None
) -> TilingStrategy:
    """Zerlegung laut Konfiguration, aber mit eigenen Teilarten (z. B. für den Rahmen).

    Die Registry-Fabriken lesen die Teilarten aus der Palette; eine Kopie der
    Konfiguration mit anderen Teilarten liefert deshalb dieselbe Strategie für andere Teile.
    ``max_part_length`` erlaubt längere Teile, z. B. Plates 16 × 16 in der Verbindungslage.
    """
    adjusted = replace(config, palette=replace(config.palette, part_kinds=part_kinds))
    if max_part_length is not None:
        adjusted = replace(adjusted, tiling=replace(config.tiling, max_part_length=max_part_length))
    return resolve(TILING_STRATEGIES, config.tiling.strategy, "Zerlegungsverfahren")(adjusted)


def build_components(config: AppConfig) -> PipelineComponents:
    """Steckt die konkreten Bausteine eines Laufs anhand der Konfiguration zusammen."""
    return PipelineComponents(
        image_preparer=PillowImagePreparer(
            fit_mode=config.mosaic.fit_mode,
            resampling=config.mosaic.resampling,
            edits=ImageEdits(
                contrast=config.mosaic.contrast,
                saturation=config.mosaic.saturation,
                brightness=config.mosaic.brightness,
                color_filter=config.mosaic.color_filter,
                filter_strength=config.mosaic.filter_strength,
                rotate=config.mosaic.rotate,
                mirror=config.mosaic.mirror,
                crop=config.mosaic.crop,
                background=config.mosaic.background,
                background_tolerance=config.mosaic.background_tolerance,
                background_amount=config.mosaic.background_amount,
            ),
        ),
        color_space=LabColorSpace(config.dithering.color_distance),
        ditherer=resolve(DITHERERS, config.dithering.method, "Dithering-Verfahren")(config),
        tiler=resolve(TILING_STRATEGIES, config.tiling.strategy, "Zerlegungsverfahren")(config),
        frame_tiler=build_tiler(config, frame_part_kinds(config)),
        support_tiler=build_tiler(config, (PartKind.PLATE,)),  # Stützlagen brauchen Noppen
        brick_tiler=build_tiler(config, (PartKind.BRICK,)),  # Relief: ein Brick statt 3 Plates
        bond_tiler=build_tiler(config, (PartKind.PLATE,), max_part_length=BOND_PART_STUDS),
        shaper=resolve(SHAPERS, config.shapes.style, "Stil")(config),
        writers=tuple(
            resolve(OUTPUT_WRITERS, name, "Ausgabeformat")(config) for name in config.outputs
        ),
    )


def build_pipeline(
    config: AppConfig,
    *,
    catalog_source: PartCatalogSource | None = None,
    updater: CatalogUpdaterProtocol | None = None,
    id_suffix: Callable[[], str] | None = None,
    observer: Callable[[MosaicResult], None] | None = None,
) -> MosaicPipeline:
    """Prüft die Konfiguration und baut die Pipeline mit allen Abhängigkeiten.

    Ohne ``catalog_source``/``updater`` kommt der Katalog aus den lokalen CSV-Dateien;
    die Cloud-Variante setzt hier PostgreSQL ein.

    Raises:
        ConfigError: Bei ungültigen Werten oder unbekannten Registry-Namen.
    """
    validate_config(config)
    validate_registry_names(config)
    return MosaicPipeline(
        config,
        catalog_source=catalog_source or build_catalog_source(config.source),
        updater=updater or build_updater(config.source),
        id_suffix=id_suffix,
        observer=observer,
        component_factory=build_components,
        method_names={
            "Dithering": tuple(DITHERERS),
            "Zerlegung": tuple(TILING_STRATEGIES),
            "Stile": tuple(SHAPERS),
            "Ausgaben": tuple(OUTPUT_WRITERS),
        },
    )


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
logger = logging.getLogger(__name__)


def serve(config: AppConfig, host: str, port: int) -> None:
    """Startet die FastAPI-App mit uvicorn (Befehl ``lego-mosaik server``)."""
    if host not in LOOPBACK_HOSTS:
        logger.warning("Achtung: %s ist von außen erreichbar und hat keine Anmeldung!", host)
    uvicorn.run(create_app(config, build_pipeline, build_price_service), host=host, port=port)


def run() -> None:
    """Entry Point aus pyproject.toml: startet die deutsche CLI."""
    create_cli(CONFIG, build_pipeline, serve, build_price_service)()
