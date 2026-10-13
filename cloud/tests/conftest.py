"""Fixtures der Cloud-Tests: echtes PostgreSQL (testcontainers), S3 über moto.

Die Fixtures des Kerns (Mini-Katalog, Testbilder, Konfiguration) werden aus
``tests/conftest.py`` übernommen, damit Kern und Cloud mit denselben Daten testen.
"""

import importlib.util
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import boto3
import pytest
from lego_mosaik.config import AppConfig
from moto import mock_aws
from prometheus_client import CollectorRegistry
from sqlalchemy import Engine, create_engine, text

from lego_mosaik_cloud.app import CloudServices, build_services
from lego_mosaik_cloud.db.engine import migrate
from lego_mosaik_cloud.db.schema import metadata
from lego_mosaik_cloud.settings import CloudSettings

CORE_TESTS = Path(__file__).resolve().parents[2] / "tests"
BUCKET = "lego-mosaik-test"
POSTGRES_IMAGE = "postgres:18-alpine"


def _load_core_conftest() -> ModuleType:
    """Lädt ``tests/conftest.py`` des Kerns unter eigenem Namen (``conftest`` ist belegt)."""
    spec = importlib.util.spec_from_file_location("kern_conftest", CORE_TESTS / "conftest.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["kern_conftest"] = module
    spec.loader.exec_module(module)
    return module


kern = _load_core_conftest()
# Fixtures des Kerns hier verfügbar machen (pytest findet sie als Attribute der conftest)
app_config = kern.app_config
source_config = kern.source_config
pipeline_config = kern.pipeline_config
image_file = kern.image_file


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """PostgreSQL im Container; ``LEGO_MOSAIK_TEST_DATABASE_URL`` nutzt eine vorhandene DB."""
    existing = os.environ.get("LEGO_MOSAIK_TEST_DATABASE_URL")
    if existing:
        yield existing
        return
    try:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer(POSTGRES_IMAGE, driver="psycopg")
        container.start()
    except Exception as error:  # Docker fehlt oder läuft nicht
        pytest.skip(f"Kein PostgreSQL für Tests verfügbar: {error}")
    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """Engine mit migriertem Schema (einmal pro Testlauf)."""
    engine = create_engine(database_url)
    migrate(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def db(engine: Engine) -> Engine:
    """Leere Tabellen vor jedem Test."""
    names = ", ".join(table.name for table in reversed(metadata.sorted_tables))
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {names} CASCADE"))
    return engine


@pytest.fixture
def s3() -> Iterator[Any]:
    """S3 im Speicher (moto) mit leerem Bucket."""
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def settings(tmp_path: Path) -> CloudSettings:
    """Einstellungen für Tests; ``teil_geometrie.csv`` schreibt die Fixture ``source_config``."""
    return CloudSettings(
        s3_bucket=BUCKET,
        data_dir=tmp_path,
        catalog_refresh_seconds=0,
        log_format="text",
    )


@pytest.fixture
def services(
    settings: CloudSettings, db: Engine, s3: Any, pipeline_config: AppConfig
) -> CloudServices:
    """Alle Cloud-Bausteine mit Test-DB, moto-S3 und eigener Metrik-Registry."""
    return build_services(
        settings, engine=db, s3=s3, registry=CollectorRegistry(), base=pipeline_config
    )


@pytest.fixture
def imported(services: CloudServices) -> CloudServices:
    """Wie ``services``, aber mit importiertem Mini-Katalog."""
    services.importer.write(kern.make_catalog(), ["colors.csv.gz", "parts.csv.gz"])
    return services
