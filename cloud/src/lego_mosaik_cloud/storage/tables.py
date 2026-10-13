"""Paletten und Presets in PostgreSQL: eine ``ItemTable`` für ``PaletteStore``/``PresetStore``.

Jeder Eintrag ist eine Zeile ``(id, daten jsonb)`` mit genau dem JSON, das lokal in
``daten/paletten.json`` bzw. ``daten/presets.json`` steht. Prüfung, Sortierung und
Namenskonflikte bleiben dadurch Sache der Stores aus dem Kern.
"""

from collections.abc import Iterator, MutableSequence
from contextlib import contextmanager
from typing import Any, cast

from lego_mosaik.domain.errors import ConfigError
from sqlalchemy import Engine, Table, delete, func, select
from sqlalchemy.dialects.postgresql import insert


class PostgresItemTable:
    """Erfüllt ``lego_mosaik.storage.protocol.ItemTable``.

    ``transaction`` sperrt die Tabelle per ``pg_advisory_xact_lock`` vom Lesen bis zum
    Schreiben – wie das Thread-Lock der JSON-Datei, aber über alle Pods hinweg.

    Args:
        engine: Datenbankverbindung.
        table: ``schema.palettes`` oder ``schema.presets``.
    """

    def __init__(self, engine: Engine, table: Table) -> None:
        self._engine = engine
        self._table = table

    def read(self) -> list[dict[str, object]]:
        with self._engine.connect() as connection:
            rows = connection.execute(select(self._table.c.daten).order_by(self._table.c.id))
            return [_item(row.daten) for row in rows]

    @contextmanager
    def transaction(self) -> Iterator[MutableSequence[dict[str, object]]]:
        table = self._table
        with self._engine.begin() as connection:
            connection.execute(select(func.pg_advisory_xact_lock(func.hashtext(table.name))))
            rows = connection.execute(select(table.c.daten).order_by(table.c.id))
            before = {str(item["id"]): item for item in (_item(row.daten) for row in rows)}
            items = [dict(item) for item in before.values()]
            yield items  # Exception im with-Block → Rollback, nichts geändert
            after = {str(item["id"]): item for item in items}
            removed = set(before) - set(after)
            if removed:
                connection.execute(delete(table).where(table.c.id.in_(removed)))
            changed = [
                {"id": key, "daten": item} for key, item in after.items() if before.get(key) != item
            ]
            if changed:
                statement = insert(table).values(changed)
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[table.c.id], set_={"daten": statement.excluded.daten}
                    )
                )


def _item(value: Any) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ConfigError("Ein Eintrag in der Datenbank ist beschädigt (kein JSON-Objekt).")
    return cast(dict[str, object], value)
