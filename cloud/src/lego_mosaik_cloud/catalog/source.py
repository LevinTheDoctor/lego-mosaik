"""Teilekatalog aus PostgreSQL (erfüllt ``PartCatalogSource``)."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.domain.models import (
    ColorCategory,
    LegoColor,
    LongAxis,
    PartCatalog,
    PartKind,
    PartShape,
    PartType,
)
from lego_mosaik.sources.rebrickable_csv import parse_hex_rgb
from sqlalchemy import Connection, Engine, select

from lego_mosaik_cloud.db.schema import available, catalog_state, colors, part_types

logger = logging.getLogger(__name__)

STATE_ID = 1  # katalog_stand hat genau eine Zeile


class PostgresCatalogSource:
    """Lädt den Katalog, den ``katalog-import`` geschrieben hat, und cacht ihn im Prozess.

    Mehrere Pods: Nach einem Import erhöht sich ``katalog_stand.version``. Jeder Pod prüft
    höchstens alle ``refresh_seconds``, ob sich die Version geändert hat, und lädt dann neu.

    Args:
        engine: Datenbankverbindung.
        refresh_seconds: Abstand der Versionsprüfung.
        clock: Monotone Uhr (in Tests austauschbar).
    """

    def __init__(
        self,
        engine: Engine,
        refresh_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._refresh = refresh_seconds
        self._clock = clock
        self._catalog: PartCatalog | None = None
        self._version: int | None = None
        self._checked = 0.0
        self.imported: datetime | None = None  # Stand des geladenen Katalogs (für Metriken)
        self._lock = threading.Lock()

    def load_catalog(self) -> PartCatalog:
        """Katalog aus dem Cache; neu geladen beim ersten Mal oder nach einem Import.

        Raises:
            CatalogError: Wenn noch kein Katalog importiert wurde.
        """
        with self._lock:
            now = self._clock()
            if self._catalog is not None and now - self._checked < self._refresh:
                return self._catalog
            with self._engine.connect() as connection:
                state = connection.execute(
                    select(catalog_state).where(catalog_state.c.id == STATE_ID)
                ).first()
                if state is None:
                    raise CatalogError(
                        "Es wurde noch kein Teilekatalog importiert "
                        "(„lego-mosaik-cloud katalog-import“ bzw. Helm-Job katalog-import)."
                    )
                if self._catalog is None or state.version != self._version:
                    self._catalog = _read_catalog(connection)
                    self._version, self.imported = state.version, state.importiert
                    logger.info("Teilekatalog Version %d geladen.", state.version)
            self._checked = now
            return self._catalog

    def invalidate(self) -> None:
        with self._lock:
            self._catalog = None
            self._version = None


def _read_catalog(connection: Connection) -> PartCatalog:
    color_map = {
        row.id: LegoColor(
            row.id,
            row.name,
            parse_hex_rgb(row.rgb_hex),
            row.transparent,
            ldraw_code=row.ldraw_code,
            category=ColorCategory(row.kategorie),
            bricklink_code=row.bricklink_code,
            lego_name=row.lego_name,
            last_year=row.letztes_jahr,
        )
        for row in connection.execute(select(colors))
    }
    part_map = {
        row.part_num: PartType(
            part_num=row.part_num,
            name=row.name,
            kind=PartKind(row.art),
            width_studs=row.breite,
            length_studs=row.laenge,
            ldraw_file=row.ldraw_datei,
            height_ldu=row.hoehe_ldu,
            ldraw_long_axis=LongAxis(row.ldraw_achse),
            bricklink_id=row.bricklink_id,
            shape=PartShape(row.form),
            outline=tuple((float(x), float(y)) for x, y in row.umriss),
        )
        for row in connection.execute(select(part_types))
    }
    combos: set[tuple[str, int]] = set()
    element_ids: dict[tuple[str, int], str] = {}
    for row in connection.execute(select(available)):
        combo = (row.part_num, row.farb_id)
        combos.add(combo)
        if row.element_id is not None:
            element_ids[combo] = row.element_id
    return PartCatalog(color_map, part_map, frozenset(combos), element_ids)
