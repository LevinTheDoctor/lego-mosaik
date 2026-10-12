"""Abgleich der eigenen Daten mit einer lokalen Bricklink-Studio-Installation.

Die Tests laufen nur, wenn Studio installiert ist (sonst übersprungen). Sie prüfen
Annahmen, die wir nicht erfinden wollen: BrickLink-Teilenummern der Whitelist.
"""

import csv
import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest

from lego_mosaik.domain.models import PartShape
from lego_mosaik.domain.shapes import inside_convex, outline, shape_size
from lego_mosaik.outputs.ldraw import QUARTER_TURNS

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


def _axis_script() -> ModuleType:
    # Das Skript liegt außerhalb des Pakets; importlib lädt es direkt aus der Datei
    script = Path(__file__).parent.parent / "scripts" / "ldraw_achsen_pruefen.py"
    spec = importlib.util.spec_from_file_location("ldraw_achsen_pruefen", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ldraw_long_axes_match_library() -> None:
    assert _axis_script().check(STUDIO_DIR / "ldraw", GEOMETRY_FILE) == []


def _shaped_rows() -> list[tuple[str, PartShape]]:
    with GEOMETRY_FILE.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        (row["ldraw_file"], PartShape(row["shape"]))
        for row in rows
        if row.get("shape") not in (None, "", PartShape.SQUARE.value)
    ]


@pytest.mark.parametrize(("ldraw_file", "shape"), _shaped_rows())
def test_shape_outlines_match_ldraw_library(ldraw_file: str, shape: PartShape) -> None:
    """Umriss aus domain/shapes.py == Umriss aus LDraw, gedreht wie in mosaik.ldr."""
    hull = _axis_script().footprint(STUDIO_DIR / "ldraw", ldraw_file)
    assert hull, f"{ldraw_file} nicht gefunden"
    size = shape_size(shape)
    axis = (np.arange(160) + 0.5) / 160 * size
    xs, ys = np.meshgrid(axis, axis)
    for turns, (a, _, c, _, _, _, g, _, i) in enumerate(QUARTER_TURNS):
        # LDraw (x, z) in LDU um die Mitte → Noppen-Koordinaten der Box
        turned = [
            ((a * x + c * z) / 20 + size / 2, (g * x + i * z) / 20 + size / 2) for x, z in hull
        ]
        mismatch = inside_convex(turned, xs, ys) != inside_convex(outline(shape, turns), xs, ys)
        # Kreise sind in LDraw und hier unterschiedlich fein unterteilt; falsche Drehung ≈ 30 %
        assert mismatch.mean() < 0.03, (ldraw_file, turns)
