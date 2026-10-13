"""Alembic-Umgebung: nutzt die Verbindung, die ``db.engine.migrate`` übergibt."""

from alembic import context
from sqlalchemy import Connection

from lego_mosaik_cloud.db.schema import metadata

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Migrationen bitte über „lego-mosaik-cloud migrieren“ starten.")

context.configure(connection=connection, target_metadata=metadata)
with context.begin_transaction():
    context.run_migrations()
