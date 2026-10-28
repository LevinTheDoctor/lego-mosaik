"""Tabellen der Cloud-Variante (SQLAlchemy Core).

Die Migrationen in ``db/migrations`` legen genau diese Tabellen an; ein Test prüft,
dass beide übereinstimmen.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    PrimaryKeyConstraint,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# --- Teilekatalog (ersetzt die Rebrickable-CSV-Dateien zur Laufzeit) ----------------

colors = Table(
    "farben",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("name", Text, nullable=False),
    Column("rgb_hex", Text, nullable=False),
    Column("transparent", Boolean, nullable=False),
    Column("ldraw_code", Integer),
    Column("kategorie", Text, nullable=False),
    Column("bricklink_code", Integer),
    Column("lego_name", Text),
    Column("letztes_jahr", Integer),
)

part_types = Table(
    "teiltypen",
    metadata,
    Column("part_num", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("art", Text, nullable=False),
    Column("breite", Integer, nullable=False),
    Column("laenge", Integer, nullable=False),
    Column("ldraw_datei", Text, nullable=False),
    Column("hoehe_ldu", Integer, nullable=False),
    Column("ldraw_achse", Text, nullable=False),
    Column("bricklink_id", Text),
    Column("form", Text, nullable=False),
    Column("umriss", JSONB, nullable=False),
)

available = Table(
    "verfuegbar",
    metadata,
    Column("part_num", Text, ForeignKey("teiltypen.part_num", ondelete="CASCADE"), nullable=False),
    Column("farb_id", Integer, ForeignKey("farben.id", ondelete="CASCADE"), nullable=False),
    Column("element_id", Text),
    PrimaryKeyConstraint("part_num", "farb_id"),
)

catalog_state = Table(
    "katalog_stand",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),  # immer 1
    Column("version", BigInteger, nullable=False),
    Column("importiert", DateTime(timezone=True), nullable=False),
    Column("dateien", JSONB, nullable=False),
)

# --- Bibliothek (ersetzt daten/paletten.json und daten/presets.json) ----------------

palettes = Table(
    "paletten",
    metadata,
    Column("id", Text, primary_key=True),
    Column("daten", JSONB, nullable=False),
)

presets = Table(
    "presets",
    metadata,
    Column("id", Text, primary_key=True),
    Column("daten", JSONB, nullable=False),
)

# --- BrickLink-Preis-Cache (ersetzt daten/bricklink/preise.json) -------------------

price_cache = Table(
    "preis_cache",
    metadata,
    Column("teil", Text, nullable=False),
    Column("farbe", Integer, nullable=False),
    Column("einstellungen", Text, nullable=False),
    Column("verkauft", Numeric),
    Column("angeboten", Numeric),
    Column("stand", DateTime(timezone=True), nullable=False),
    PrimaryKeyConstraint("teil", "farbe", "einstellungen"),
)

# --- Mosaike (Dateien liegen auf dem Volume, hier Metadaten und ergebnis.json) ------

mosaics = Table(
    "mosaike",
    metadata,
    Column("id", Text, primary_key=True),
    Column("titel", Text, nullable=False),
    Column("basis", Text),
    Column("erstellt", DateTime(timezone=True), nullable=False),
    Column("ergebnis", JSONB, nullable=False),
    Column("dateien", JSONB, nullable=False),  # alle Dateien im Ordner, auch interne
    Column("bytes", BigInteger, nullable=False),
    Index("mosaike_erstellt", "erstellt"),
)
