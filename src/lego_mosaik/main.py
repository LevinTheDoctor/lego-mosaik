"""Composition Root: einzige Stelle, an der konfiguriert und verdrahtet wird.

Nur dieses Modul kennt die konkreten Klassen und übersetzt die Konfiguration in
deren Konstruktorparameter. Alle anderen Module kennen nur Protocols und Domain.
Die vollständige Konfiguration (``CONFIG``) und die Pipeline folgen mit der CLI.
"""

import sys

lazy import httpx

from lego_mosaik.config import SourceConfig
lazy from lego_mosaik.sources.rebrickable_csv import RebrickableCsvSource
lazy from lego_mosaik.sources.updater import CatalogUpdater


def build_catalog_source(source: SourceConfig) -> RebrickableCsvSource:
    """Erzeugt die CSV-Datenquelle aus der Konfiguration."""
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


def run() -> None:
    """Entry Point aus pyproject.toml: startet die deutsche CLI."""
    # sys.exit mit einem String gibt die Meldung auf stderr aus und beendet mit Code 1
    sys.exit("Die CLI ist noch nicht implementiert.")
