"""BrickLink-Preis-Cache in PostgreSQL (statt ``daten/bricklink/preise.json``)."""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from lego_mosaik.pricing.protocol import PriceGuide, PriceKey
from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert

from lego_mosaik_cloud.db.schema import price_cache


class PostgresPriceCache:
    """Erfüllt ``PriceCacheProtocol``; alle Pods teilen sich die Preise.

    Args:
        engine: Datenbankverbindung.
        settings: Kennung der Abfrage-Einstellungen (``price_cache_key``).
        max_age_days: Ab diesem Alter gilt ein Preis als veraltet.
        now: Uhr (Tests: feste Zeit).
    """

    def __init__(
        self,
        engine: Engine,
        settings: str,
        max_age_days: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._max_age = timedelta(days=max_age_days)
        self._now = now

    def get(self, key: PriceKey) -> PriceGuide | None:
        item, color = key
        query = select(price_cache).where(
            price_cache.c.teil == item,
            price_cache.c.farbe == color,
            price_cache.c.einstellungen == self._settings,
        )
        with self._engine.connect() as connection:
            row = connection.execute(query).first()
        if row is None:
            return None
        return PriceGuide(sold=row.verkauft, stock=row.angeboten, fetched=row.stand)

    def is_fresh(self, guide: PriceGuide) -> bool:
        return self._now() - guide.fetched <= self._max_age

    def update(self, guides: Mapping[PriceKey, PriceGuide]) -> None:
        if not guides:
            return
        rows = [
            {
                "teil": item,
                "farbe": color,
                "einstellungen": self._settings,
                "verkauft": guide.sold,
                "angeboten": guide.stock,
                "stand": guide.fetched,
            }
            for (item, color), guide in guides.items()
        ]
        statement = insert(price_cache).values(rows)
        upsert = statement.on_conflict_do_update(
            index_elements=[price_cache.c.teil, price_cache.c.farbe, price_cache.c.einstellungen],
            set_={
                "verkauft": statement.excluded.verkauft,
                "angeboten": statement.excluded.angeboten,
                "stand": statement.excluded.stand,
            },
        )
        with self._engine.begin() as connection:
            connection.execute(upsert)
