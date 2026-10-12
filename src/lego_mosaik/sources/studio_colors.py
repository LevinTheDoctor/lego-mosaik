"""Farbtabelle von Bricklink Studio (optional) und Farbkategorien.

Studio bringt in ``data/StudioColorDefinition.txt`` (Tab-getrennt) eine Tabelle mit,
die pro Farbe den LDraw-Code, den BrickLink-Code, den offiziellen LEGO-Namen und
eine Kategorie (Solid, Metallic, Pearl, …) enthält.

Warum der Abgleich über den Namen? Rebrickable-IDs stimmen *nicht immer* mit den
LDraw-Codes überein (z. B. Rebrickable 326 "Olive Green" = LDraw 330, während
LDraw 326 "Yellowish Green" ist). Die Rebrickable-Namen folgen aber den BrickLink-
Namen, die in Studios Tabelle stehen. Darüber finden wir den richtigen LDraw-Code.
"""

import csv
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lego_mosaik.domain.errors import CatalogError
from lego_mosaik.domain.models import ColorCategory

logger = logging.getLogger(__name__)

COLUMN_LDRAW_CODE = "LDraw Color Code"
COLUMN_LDRAW_NAME = "LDraw Color Name"
COLUMN_BL_CODE = "BL Color Code"
COLUMN_BL_NAME = "BL Color Name"
COLUMN_LEGO_NAME = "LDD Color Name"
COLUMN_CATEGORY = "CategoryName"
REQUIRED_COLUMNS = (
    COLUMN_LDRAW_CODE,
    COLUMN_LDRAW_NAME,
    COLUMN_BL_CODE,
    COLUMN_BL_NAME,
    COLUMN_LEGO_NAME,
    COLUMN_CATEGORY,
)
CATEGORY_SUFFIX = " colors"  # Studio schreibt z. B. "Metallic Colors"


@dataclass(frozen=True, slots=True)
class StudioColor:
    """Eine Zeile aus Studios Farbtabelle (nur die benötigten Werte)."""

    ldraw_code: int
    bricklink_code: int | None
    lego_name: str | None
    category: ColorCategory | None


class StudioColorTable:
    """Nachschlagetabelle über BrickLink-Name, LDraw-Name und LDraw-Code."""

    def __init__(self, rows: Sequence[tuple[str, str, StudioColor]]) -> None:
        self._by_bl_name: dict[str, StudioColor] = {}
        self._by_ldraw_name: dict[str, StudioColor] = {}
        self._by_code: dict[int, StudioColor] = {}
        for bl_name, ldraw_name, color in rows:
            # setdefault behält den ersten Eintrag; Studio listet die Hauptzeile zuerst
            if bl_name:
                self._by_bl_name.setdefault(normalize_color_name(bl_name), color)
            self._by_ldraw_name.setdefault(normalize_color_name(ldraw_name), color)
            self._by_code.setdefault(color.ldraw_code, color)

    def find_by_name(self, rebrickable_name: str) -> StudioColor | None:
        """Sucht zuerst über den BrickLink-Namen, dann über den LDraw-Namen."""
        key = normalize_color_name(rebrickable_name)
        return self._by_bl_name.get(key) or self._by_ldraw_name.get(key)

    def find_by_code(self, ldraw_code: int) -> StudioColor | None:
        """Sucht über den LDraw-Code (z. B. nach einem Override)."""
        return self._by_code.get(ldraw_code)


def normalize_color_name(name: str) -> str:
    """Vereinheitlicht Schreibweisen: "Light_Bluish_Grey" → "light bluish gray"."""
    text = name.strip().lower().replace("_", " ").replace("-", " ")
    # split() ohne Argument trennt an beliebig vielen Leerzeichen; join setzt genau eines
    return " ".join(text.replace("grey", "gray").split())


def read_studio_colors(path: Path | None) -> StudioColorTable | None:
    """Liest Studios Farbtabelle; ``None``, wenn kein Pfad angegeben ist oder die Datei fehlt."""
    if path is None or not path.is_file():
        return None
    # utf-8-sig verträgt ein mögliches BOM am Dateianfang
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise CatalogError(f"In {path.name} fehlen die Spalten: {', '.join(missing)}.")
        rows = [_parse_row(row) for row in reader if row[COLUMN_LDRAW_CODE].strip()]
    logger.info("Studio-Farbtabelle geladen: %d Einträge.", len(rows))
    return StudioColorTable(rows)


def _parse_row(row: dict[str, str]) -> tuple[str, str, StudioColor]:
    """Eine TSV-Zeile → (BrickLink-Name, LDraw-Name, StudioColor)."""
    bl_code = row[COLUMN_BL_CODE].strip()
    color = StudioColor(
        ldraw_code=int(row[COLUMN_LDRAW_CODE]),
        # Bedingter Ausdruck: leere Zelle → None, sonst Zahl
        bricklink_code=int(bl_code) if bl_code else None,
        lego_name=row[COLUMN_LEGO_NAME].strip() or None,  # "" or None ergibt None
        category=parse_studio_category(row[COLUMN_CATEGORY]),
    )
    return row[COLUMN_BL_NAME].strip(), row[COLUMN_LDRAW_NAME].strip(), color


def parse_studio_category(text: str) -> ColorCategory | None:
    """ "Metallic Colors" → ``ColorCategory.METALLIC``; Unbekanntes → ``None``."""
    value = text.strip().lower().removesuffix(CATEGORY_SUFFIX)
    # Mitgliedschaftstest auf den Werten des Enums (StrEnum-Werte sind Strings)
    return ColorCategory(value) if value in set(ColorCategory) else None


def category_from_keywords(
    name: str, is_transparent: bool, keywords: Sequence[tuple[str, ColorCategory]]
) -> ColorCategory:
    """Bestimmt die Kategorie aus Schlüsselwörtern im Farbnamen (Fallback ohne Studio).

    Die Reihenfolge der Schlüsselwörter zählt: "Glitter Trans-Clear" soll GLITTER
    werden, nicht TRANSPARENT, deshalb steht "Glitter" vor "Trans".
    """
    lowered = name.lower()
    for keyword, category in keywords:
        if keyword.lower() in lowered:
            return category
    return ColorCategory.TRANSPARENT if is_transparent else ColorCategory.SOLID
