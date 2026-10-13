"""Tests der PostgreSQL-Speicher: Schema, Paletten/Presets und Preis-Cache."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from lego_mosaik.domain.errors import ConfigError, NotFoundError
from lego_mosaik.domain.library import PresetOrder
from lego_mosaik.pricing.protocol import PriceGuide
from lego_mosaik.storage.palettes import PaletteStore
from lego_mosaik.storage.presets import PresetStore
from sqlalchemy import Engine

from lego_mosaik_cloud.db import schema
from lego_mosaik_cloud.storage.prices import PostgresPriceCache
from lego_mosaik_cloud.storage.tables import PostgresItemTable

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def test_migrations_match_schema(db: Engine) -> None:
    with db.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, schema.metadata) == []


def test_palettes_crud(db: Engine) -> None:
    store = PaletteStore(PostgresItemTable(db, schema.palettes))
    created = store.create("Graustufen", "nur Grau", [0, 15, 71, 0])
    assert created.color_ids == (0, 15, 71)
    assert [p.id for p in store.list()] == ["graustufen"]
    with pytest.raises(ConfigError):
        store.create("graustufen", "", [0])  # gleicher Name
    store.update("graustufen", "Grau", "neu", [15])
    assert store.get("Grau").color_ids == (15,)
    store.delete("graustufen")
    with pytest.raises(NotFoundError):
        store.get("graustufen")


def test_failed_transaction_changes_nothing(db: Engine) -> None:
    table = PostgresItemTable(db, schema.palettes)
    PaletteStore(table).create("Rot", "", [4])
    with pytest.raises(RuntimeError), table.transaction() as items:
        items.clear()
        raise RuntimeError("Abbruch")
    assert [item["id"] for item in table.read()] == ["rot"]


def test_presets_rank_and_count_uses(db: Engine) -> None:
    store = PresetStore(PostgresItemTable(db, schema.presets), now=lambda: NOW)
    store.create("Klein", "", {"breite": 16}, rating=3)
    store.create("Groß", "", {"breite": 48}, favorite=True)
    store.record_use("klein")
    store.mark("klein", rating=5)
    ranked = store.list(PresetOrder.RATING)
    assert [p.id for p in ranked] == ["gross", "klein"]  # Favorit zuerst
    klein = store.get("klein")
    assert klein.uses == 1 and klein.rating == 5 and klein.settings == {"breite": 16}


def test_price_cache_upsert_and_freshness(db: Engine) -> None:
    cache = PostgresPriceCache(db, "N|EUR|europe|-|Y", max_age_days=7, now=lambda: NOW)
    key = ("3024", 11)
    assert cache.get(key) is None
    cache.update({key: PriceGuide(Decimal("0.05"), None, NOW - timedelta(days=10))})
    old = cache.get(key)
    assert old is not None and old.sold == Decimal("0.05") and not cache.is_fresh(old)
    cache.update({key: PriceGuide(Decimal("0.07"), Decimal("0.09"), NOW)})
    new = cache.get(key)
    assert new is not None and new.stock == Decimal("0.09") and cache.is_fresh(new)
    other = PostgresPriceCache(db, "U|USD|-|-|N", max_age_days=7)
    assert other.get(key) is None  # andere Einstellungen, eigener Schlüssel
