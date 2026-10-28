"""Befehle der Cloud-Variante: ``lego-mosaik-cloud server|migrieren|katalog-import|…``.

Im Helm-Chart: ``migrieren`` im initContainer, ``katalog-import`` als Job/CronJob,
``server`` im Deployment. ``uebernehmen`` kopiert lokale Daten (Paletten, Presets,
Mosaike aus ``ausgabe/``) einmalig in PostgreSQL und auf das Mosaik-Volume.
"""

import logging
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from lego_mosaik.outputs.result_json import FILE_NAME as RESULT_FILE
from lego_mosaik.storage.json_file import JsonFile

from lego_mosaik_cloud.app import CloudServices, build_cloud_app, build_services
from lego_mosaik_cloud.db import schema
from lego_mosaik_cloud.db.engine import migrate, wait_for_database
from lego_mosaik_cloud.observability.logging import configure_logging
from lego_mosaik_cloud.settings import CloudSettings
from lego_mosaik_cloud.storage.tables import PostgresItemTable

logger = logging.getLogger(__name__)

cli = typer.Typer(
    help="LEGO-Mosaik-Service in der Cloud (PostgreSQL, Mosaik-Volume, Actuator).",
    no_args_is_help=True,
    add_completion=False,
)

WaitOption = Annotated[
    float, typer.Option("--warten", help="Sekunden auf die Datenbank warten (0 = sofort).")
]


def _services(wait_seconds: float = 0) -> CloudServices:
    settings = CloudSettings()
    configure_logging(settings.log_level, settings.log_format)
    services = build_services(settings)
    if wait_seconds > 0:
        wait_for_database(services.engine, wait_seconds)
    return services


@cli.command()
def server(
    migrieren: Annotated[
        bool, typer.Option(help="Vor dem Start das Datenbankschema aktualisieren.")
    ] = False,
    warten: WaitOption = 0,
) -> None:
    """Startet die HTTP-API (Port aus LEGO_MOSAIK_PORT, Standard 8080)."""
    services = _services(warten)
    if migrieren:
        migrate(services.engine)
    app = build_cloud_app(services)
    settings = services.settings
    # log_config=None: uvicorn nutzt die JSON-Logs; proxy_headers: richtige URLs hinter Ingress
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


@cli.command()
def migrieren(warten: WaitOption = 60) -> None:
    """Bringt das Datenbankschema auf den neuesten Stand (Alembic)."""
    migrate(_services(warten).engine)


@cli.command("katalog-import")
def catalog_import(
    erzwingen: Annotated[
        bool, typer.Option(help="Auch importieren, wenn der Katalog noch aktuell ist.")
    ] = False,
    warten: WaitOption = 60,
) -> None:
    """Lädt die Rebrickable-Dumps und schreibt den Teilekatalog nach PostgreSQL."""
    report = _services(warten).importer.update(force=erzwingen)
    for update in report.files:
        logger.info("%s: %s (%s)", update.file_name, update.status.value, update.message)


@cli.command()
def uebernehmen(
    daten: Annotated[Path, typer.Option(help="Ordner mit paletten.json und presets.json.")] = Path(
        "daten"
    ),
    ausgabe: Annotated[
        Path | None, typer.Option(help="Ordner mit lokalen Mosaiken (z. B. ausgabe).")
    ] = None,
    warten: WaitOption = 60,
) -> None:
    """Übernimmt lokale Paletten, Presets und Mosaike (vorhandene bleiben unverändert)."""
    services = _services(warten)
    for file_name, key, table in (
        ("paletten.json", "paletten", schema.palettes),
        ("presets.json", "presets", schema.presets),
    ):
        path = daten / file_name
        if not path.is_file():
            continue
        local = JsonFile(path, key).read()
        with PostgresItemTable(services.engine, table).transaction() as items:
            known = {item["id"] for item in items}
            new = [item for item in local if item.get("id") not in known]
            items.extend(new)
        logger.info("%s: %d übernommen, %d schon vorhanden.", key, len(new), len(local) - len(new))
    if ausgabe is None:
        return
    count = 0
    for directory in sorted(path.parent for path in ausgabe.glob(f"*/{RESULT_FILE}")):
        if services.mosaics.exists(directory.name):
            continue
        services.mosaics.store(directory)
        count += 1
    logger.info("Mosaike: %d übernommen.", count)


def run() -> None:
    """Entry Point aus pyproject.toml."""
    cli()
