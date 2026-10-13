"""Startschema: Katalog, Bibliothek, Preis-Cache und Mosaike.

Revision ID: 0001
Revises:
Erstellt: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farben",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("rgb_hex", sa.Text, nullable=False),
        sa.Column("transparent", sa.Boolean, nullable=False),
        sa.Column("ldraw_code", sa.Integer),
        sa.Column("kategorie", sa.Text, nullable=False),
        sa.Column("bricklink_code", sa.Integer),
        sa.Column("lego_name", sa.Text),
        sa.Column("letztes_jahr", sa.Integer),
    )
    op.create_table(
        "teiltypen",
        sa.Column("part_num", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("art", sa.Text, nullable=False),
        sa.Column("breite", sa.Integer, nullable=False),
        sa.Column("laenge", sa.Integer, nullable=False),
        sa.Column("ldraw_datei", sa.Text, nullable=False),
        sa.Column("hoehe_ldu", sa.Integer, nullable=False),
        sa.Column("ldraw_achse", sa.Text, nullable=False),
        sa.Column("bricklink_id", sa.Text),
        sa.Column("form", sa.Text, nullable=False),
        sa.Column("umriss", JSONB, nullable=False),
    )
    op.create_table(
        "verfuegbar",
        sa.Column(
            "part_num",
            sa.Text,
            sa.ForeignKey("teiltypen.part_num", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "farb_id", sa.Integer, sa.ForeignKey("farben.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("element_id", sa.Text),
        sa.PrimaryKeyConstraint("part_num", "farb_id"),
    )
    op.create_table(
        "katalog_stand",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("version", sa.BigInteger, nullable=False),
        sa.Column("importiert", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dateien", JSONB, nullable=False),
    )
    for name in ("paletten", "presets"):
        op.create_table(
            name,
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("daten", JSONB, nullable=False),
        )
    op.create_table(
        "preis_cache",
        sa.Column("teil", sa.Text, nullable=False),
        sa.Column("farbe", sa.Integer, nullable=False),
        sa.Column("einstellungen", sa.Text, nullable=False),
        sa.Column("verkauft", sa.Numeric),
        sa.Column("angeboten", sa.Numeric),
        sa.Column("stand", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("teil", "farbe", "einstellungen"),
    )
    op.create_table(
        "mosaike",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("titel", sa.Text, nullable=False),
        sa.Column("basis", sa.Text),
        sa.Column("erstellt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ergebnis", JSONB, nullable=False),
        sa.Column("dateien", JSONB, nullable=False),
        sa.Column("bytes", sa.BigInteger, nullable=False),
    )
    op.create_index("mosaike_erstellt", "mosaike", ["erstellt"])


def downgrade() -> None:
    op.drop_index("mosaike_erstellt", "mosaike")
    for name in (
        "mosaike",
        "preis_cache",
        "presets",
        "paletten",
        "katalog_stand",
        "verfuegbar",
        "teiltypen",
        "farben",
    ):
        op.drop_table(name)
