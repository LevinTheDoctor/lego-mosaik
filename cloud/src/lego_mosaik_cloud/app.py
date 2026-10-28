"""Composition Root der Cloud-Variante: PostgreSQL, Mosaik-Volume und Observability verdrahten.

Die API selbst kommt unverändert aus dem Kern (``lego_mosaik.interfaces.api.create_app``);
hier werden nur andere Speicher eingesetzt und Actuator, Metriken und Tracing ergänzt.
"""

import os
import secrets
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from fastapi import FastAPI
from lego_mosaik.config import AppConfig
from lego_mosaik.interfaces.api import ApiBackend, create_app
from lego_mosaik.main import CONFIG, build_pipeline, build_price_service
from lego_mosaik.pipeline import MosaicPipeline
from lego_mosaik.pricing.cache import price_cache_key
from lego_mosaik.pricing.service import PriceService
from lego_mosaik.storage.library import Library
from lego_mosaik.storage.palettes import PaletteStore
from lego_mosaik.storage.presets import PresetStore
from prometheus_client import REGISTRY, CollectorRegistry
from sqlalchemy import Engine, text

from lego_mosaik_cloud.catalog.importer import PostgresCatalogImporter
from lego_mosaik_cloud.catalog.source import PostgresCatalogSource
from lego_mosaik_cloud.config import cloud_app_config
from lego_mosaik_cloud.db import schema
from lego_mosaik_cloud.db.engine import build_engine
from lego_mosaik_cloud.observability.actuator import (
    Health,
    HealthGroup,
    HealthIndicator,
    Status,
    actuator_router,
)
from lego_mosaik_cloud.observability.metrics import HttpMetricsMiddleware, Metrics
from lego_mosaik_cloud.observability.tracing import configure_tracing
from lego_mosaik_cloud.settings import CloudSettings
from lego_mosaik_cloud.storage.mosaics import VolumeMosaicRepository
from lego_mosaik_cloud.storage.prices import PostgresPriceCache
from lego_mosaik_cloud.storage.tables import PostgresItemTable

ID_SUFFIX_BYTES = 3  # 6 Hex-Zeichen: Pods erzeugen nie dieselbe ID


@dataclass(frozen=True, slots=True)
class CloudServices:
    """Alle Bausteine der Cloud-Variante (auch für CLI und Tests)."""

    settings: CloudSettings
    config: AppConfig
    engine: Engine
    catalog: PostgresCatalogSource
    importer: PostgresCatalogImporter
    library: Library
    mosaics: VolumeMosaicRepository
    metrics: Metrics

    def pipeline(self, config: AppConfig) -> MosaicPipeline:
        """Pipeline mit PostgreSQL-Katalog, eindeutigen IDs und Metriken."""
        return build_pipeline(
            config,
            catalog_source=self.catalog,
            updater=self.importer,
            id_suffix=lambda: secrets.token_hex(ID_SUFFIX_BYTES),
            observer=self.metrics.observe_result,
        )

    def price_service(self, config: AppConfig) -> PriceService:
        """Preisschätzung mit dem gemeinsamen Preis-Cache in PostgreSQL."""
        cache = PostgresPriceCache(
            self.engine, price_cache_key(config.pricing), config.pricing.max_age_days
        )
        return build_price_service(config, cache=cache)


def build_services(
    settings: CloudSettings,
    *,
    engine: Engine | None = None,
    registry: CollectorRegistry = REGISTRY,
    base: AppConfig = CONFIG,
) -> CloudServices:
    """Baut alle Bausteine aus den Einstellungen.

    ``engine``, ``registry`` und ``base`` (fachliche Konfiguration) sind für Tests
    austauschbar.
    """
    engine = engine or build_engine(settings)
    config = cloud_app_config(settings, base)
    return CloudServices(
        settings=settings,
        config=config,
        engine=engine,
        catalog=PostgresCatalogSource(engine, settings.catalog_refresh_seconds),
        importer=PostgresCatalogImporter(engine, config.source),
        library=Library(
            PaletteStore(PostgresItemTable(engine, schema.palettes)),
            PresetStore(PostgresItemTable(engine, schema.presets)),
        ),
        mosaics=VolumeMosaicRepository(engine, settings.mosaic_dir),
        metrics=Metrics(registry),
    )


def build_cloud_app(services: CloudServices) -> FastAPI:
    """Die API des Kerns mit Cloud-Speichern, Actuator, Metriken und Tracing."""
    backend = ApiBackend(services.library, services.mosaics, catalog_auto_update=False)
    app = create_app(services.config, services.pipeline, services.price_service, backend=backend)
    app.add_middleware(HttpMetricsMiddleware, metrics=services.metrics)
    app.include_router(
        actuator_router(_health_group(services), services.metrics, partial(_info, services))
    )
    configure_tracing(app, services.engine, services.settings.service_name)
    return app


def _health_group(services: CloudServices) -> HealthGroup:
    indicators: dict[str, HealthIndicator] = {
        "db": partial(_database_health, services.engine),
        "speicher": partial(_storage_health, services.mosaics.root),
        "katalog": partial(_catalog_health, services),
    }
    return HealthGroup(indicators, readiness=frozenset({"db", "speicher"}))


def _database_health(engine: Engine) -> Health:
    with engine.connect() as connection:
        server = connection.execute(text("SHOW server_version")).scalar()
    return Health(Status.UP, {"datenbank": "PostgreSQL", "version": server})


def _storage_health(root: Path) -> Health:
    """``DOWN``, wenn das Volume fehlt oder nicht beschreibbar ist (Platz nur als Detail)."""
    if not root.is_dir() or not os.access(root, os.W_OK):
        return Health(Status.DOWN, {"pfad": str(root), "fehler": "nicht beschreibbar"})
    usage = shutil.disk_usage(root)
    return Health(Status.UP, {"pfad": str(root), "frei": usage.free, "gesamt": usage.total})


def _catalog_health(services: CloudServices) -> Health:
    """Ohne Import ``UNKNOWN`` (nicht ``DOWN``): Der Pod soll trotzdem bereit werden."""
    files = services.importer.status()
    imported = next((info.modified for info in files if info.modified), None)
    if imported is None:
        return Health(Status.UNKNOWN, {"hinweis": "Noch kein Katalog importiert."})
    services.metrics.catalog_imported.set(imported.timestamp())
    outdated = any(info.outdated for info in files)
    return Health(Status.UP, {"importiert": imported.isoformat(), "veraltet": outdated})


def _info(services: CloudServices) -> Mapping[str, object]:
    settings = services.settings
    imported = services.catalog.imported
    return {
        "build": {"git_sha": settings.git_sha, "zeit": settings.build_time},
        "katalog": {"geladen": imported.isoformat() if imported else None},
    }
