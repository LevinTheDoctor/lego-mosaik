"""Katalog-Import: Rebrickable-Dumps laden, mit dem Kern parsen und in PostgreSQL schreiben.

Läuft als Helm-Job/CronJob (``lego-mosaik-cloud katalog-import``) oder über
``POST /teile/aktualisieren``. Gelesen wird mit genau demselben Code wie lokal
(``RebrickableCsvSource``), geschrieben wird der fertige ``PartCatalog``.
"""

import logging
import tempfile
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.models import LegoColor, PartCatalog, PartType
from lego_mosaik.sources.protocol import (
    CatalogUpdaterProtocol,
    FileInfo,
    FileStatus,
    FileUpdate,
    UpdateReport,
)
from lego_mosaik.sources.rebrickable_csv import RebrickableCsvSource
from lego_mosaik.sources.updater import CatalogUpdater
from sqlalchemy import Connection, Engine, Row, Table, delete, insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lego_mosaik_cloud.catalog.source import STATE_ID
from lego_mosaik_cloud.db.schema import available, catalog_state, colors, part_types

logger = logging.getLogger(__name__)

BATCH_ROWS = 5000

type UpdaterFactory = Callable[[SourceConfig], CatalogUpdaterProtocol]


def _default_updater(source: SourceConfig) -> CatalogUpdater:
    return CatalogUpdater(
        data_dir=source.data_dir,
        base_url=source.base_url,
        file_names=source.files,
        optional_file_names=source.optional_files,
        max_age_days=source.max_age_days,
        timeout_seconds=source.timeout_seconds,
        user_agent=source.user_agent,
    )


class PostgresCatalogImporter:
    """Erfüllt ``CatalogUpdaterProtocol`` für die Cloud.

    Args:
        engine: Datenbankverbindung.
        source: Einstellungen der Quelle (URLs, Dateien, Geometrie, Alter).
        updater_factory: Baut den Download (Tests: ohne Netz).
        now: Uhr (UTC).
    """

    def __init__(
        self,
        engine: Engine,
        source: SourceConfig,
        updater_factory: UpdaterFactory = _default_updater,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = engine
        self._source = source
        self._updater_factory = updater_factory
        self._now = now

    def status(self) -> tuple[FileInfo, ...]:
        """Stand des letzten Imports je Datei (fehlt er, gelten alle Dateien als veraltet)."""
        state = self._state()
        names = (*self._source.files, *self._source.optional_files)
        if state is None:
            return tuple(FileInfo(name, None, True) for name in names)
        imported: datetime = state.importiert
        outdated = self._now() - imported > timedelta(days=self._source.max_age_days)
        listed = [str(entry) for entry in state.dateien]
        return tuple(FileInfo(name, imported, outdated) for name in listed)

    def update(self, *, force: bool = False) -> UpdateReport:
        """Importiert neu, wenn der Katalog fehlt, veraltet ist oder ``force`` gesetzt ist.

        Raises:
            DownloadError: Wenn Pflichtdateien nicht geladen werden können.
            CatalogError: Wenn die Dateien fehlerhaft sind.
        """
        state = self._state()
        if not force and state is not None:
            age = self._now() - state.importiert
            if age <= timedelta(days=self._source.max_age_days):
                return UpdateReport(
                    tuple(
                        FileUpdate(str(name), FileStatus.UP_TO_DATE, "Katalog ist aktuell.", True)
                        for name in state.dateien
                    )
                )
        with tempfile.TemporaryDirectory(prefix="rebrickable-") as directory:
            source = replace(self._source, data_dir=Path(directory))
            report = self._updater_factory(source).update(force=True)
            report.raise_if_unusable()
            catalog = RebrickableCsvSource(
                data_dir=source.data_dir,
                geometry_file=source.geometry_file,
                color_mapping_file=source.color_mapping_file,
                studio_color_file=source.studio_color_file,
                category_keywords=source.category_keywords,
                recent_years=source.recent_years,
            ).load_catalog()
            files = [u.file_name for u in report.files if u.status is not FileStatus.FAILED]
            self.write(catalog, files)
        return report

    def write(self, catalog: PartCatalog, files: list[str]) -> int:
        """Ersetzt den Katalog in einer Transaktion und erhöht die Version.

        Returns:
            Die neue Version.
        """
        with self._engine.begin() as connection:
            connection.execute(delete(available))
            connection.execute(delete(part_types))
            connection.execute(delete(colors))
            _insert_batches(connection, colors, [_color_row(c) for c in catalog.colors.values()])
            _insert_batches(
                connection, part_types, [_part_row(p) for p in catalog.part_types.values()]
            )
            _insert_batches(
                connection,
                available,
                [
                    {
                        "part_num": part,
                        "farb_id": color,
                        "element_id": catalog.element_ids.get((part, color)),
                    }
                    for part, color in sorted(catalog.available)
                ],
            )
            previous = connection.execute(
                select(catalog_state.c.version).where(catalog_state.c.id == STATE_ID)
            ).scalar()
            version = (previous or 0) + 1
            statement = pg_insert(catalog_state).values(
                id=STATE_ID, version=version, importiert=self._now(), dateien=files
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[catalog_state.c.id],
                    set_={
                        "version": statement.excluded.version,
                        "importiert": statement.excluded.importiert,
                        "dateien": statement.excluded.dateien,
                    },
                )
            )
        logger.info(
            "Teilekatalog Version %d importiert: %d Farben, %d Teiltypen, %d Kombinationen.",
            version,
            len(catalog.colors),
            len(catalog.part_types),
            len(catalog.available),
        )
        return version

    def _state(self) -> Row[Any] | None:
        with self._engine.connect() as connection:
            return connection.execute(
                select(catalog_state).where(catalog_state.c.id == STATE_ID)
            ).first()


def _insert_batches(connection: Connection, table: Table, rows: list[dict[str, object]]) -> None:
    """Fügt Zeilen in Paketen ein (executemany), damit große Kataloge nicht ein Statement sind."""
    for start in range(0, len(rows), BATCH_ROWS):
        connection.execute(insert(table), rows[start : start + BATCH_ROWS])


def _color_row(color: LegoColor) -> dict[str, object]:
    red, green, blue = color.rgb
    return {
        "id": color.id,
        "name": color.name,
        "rgb_hex": f"{red:02X}{green:02X}{blue:02X}",
        "transparent": color.is_transparent,
        "ldraw_code": color.ldraw_code,
        "kategorie": color.category.value,
        "bricklink_code": color.bricklink_code,
        "lego_name": color.lego_name,
        "letztes_jahr": color.last_year,
    }


def _part_row(part: PartType) -> dict[str, object]:
    return {
        "part_num": part.part_num,
        "name": part.name,
        "art": part.kind.value,
        "breite": part.width_studs,
        "laenge": part.length_studs,
        "ldraw_datei": part.ldraw_file,
        "hoehe_ldu": part.height_ldu,
        "ldraw_achse": part.ldraw_long_axis.value,
        "bricklink_id": part.bricklink_id,
        "form": part.shape.value,
        "umriss": [list(point) for point in part.outline],
    }
