"""Abgleich der eigenen Daten mit einer lokalen Bricklink-Studio-Installation.

Die Tests laufen nur, wenn Studio installiert ist (sonst übersprungen). Sie prüfen
Annahmen, die wir nicht erfinden wollen: BrickLink-Teilenummern der Whitelist.
"""

import csv
from pathlib import Path

import pytest

STUDIO_DIR = Path("/Applications/Studio 2.0")
PART_DEFINITION = STUDIO_DIR / "data" / "StudioPartDefinition2.txt"
GEOMETRY_FILE = Path(__file__).parent.parent / "daten" / "teil_geometrie.csv"

pytestmark = pytest.mark.skipif(not PART_DEFINITION.is_file(), reason="Studio nicht installiert")


def _studio_bricklink_ids() -> dict[str, set[str]]:
    """LDraw-Datei → BrickLink-Nummern (ohne bedruckte Varianten)."""
    mapping: dict[str, set[str]] = {}
    with PART_DEFINITION.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            if row["IsDecorated"] == "False":
                mapping.setdefault(row["LDraw ItemNo"], set()).add(row["BL ItemNo"])
    return mapping


def test_bricklink_ids_match_studio() -> None:
    studio = _studio_bricklink_ids()
    with GEOMETRY_FILE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    mismatches = [
        (row["part_num"], row["bricklink_id"], sorted(studio.get(row["ldraw_file"], set())))
        for row in rows
        if row["bricklink_id"] not in studio.get(row["ldraw_file"], set())
    ]
    assert mismatches == []
