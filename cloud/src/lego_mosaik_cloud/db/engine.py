"""Datenbankverbindung und Migrationen (Alembic, vergleichbar mit Flyway bei Spring)."""

import logging
import time
from importlib.resources import files

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError

from lego_mosaik_cloud.settings import CloudSettings

logger = logging.getLogger(__name__)

# Fester Schlüssel für pg_advisory_lock: Startet mehrere Pods gleichzeitig, migriert nur einer
MIGRATION_LOCK_KEY = 7_221_843_301
RETRY_SECONDS = 2.0


def build_engine(settings: CloudSettings) -> Engine:
    """Engine mit Connection-Pool; ``pool_pre_ping`` erkennt abgerissene Verbindungen."""
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
    )


def wait_for_database(engine: Engine, timeout_seconds: float) -> None:
    """Wartet, bis die Datenbank Verbindungen annimmt (z. B. im initContainer).

    Raises:
        OperationalError: Wenn sie nach ``timeout_seconds`` noch nicht erreichbar ist.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return
        except OperationalError:
            if time.monotonic() >= deadline:
                raise
            logger.info("Datenbank noch nicht erreichbar, neuer Versuch in %.0f s.", RETRY_SECONDS)
            time.sleep(RETRY_SECONDS)


def alembic_config() -> Config:
    """Alembic-Konfiguration ohne ``alembic.ini``: Skripte liegen im Paket."""
    config = Config()
    config.set_main_option(
        "script_location", str(files("lego_mosaik_cloud.db").joinpath("migrations"))
    )
    return config


def migrate(engine: Engine, revision: str = "head") -> None:
    """Bringt das Schema auf ``revision``; parallel startende Pods warten aufeinander."""
    config = alembic_config()
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        try:
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
            connection.commit()
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
            connection.commit()
    logger.info("Datenbankschema ist auf dem Stand %s.", revision)
