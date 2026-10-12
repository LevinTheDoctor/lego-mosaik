"""Erzeugt Prüfdateien für die LDraw-Ausrichtung in Bricklink Studio.

Aufruf::

    uv run python scripts/orientierung.py

Ergebnis in ``ausgabe/orientierung/``:

- ``mosaik.ldr``: Testmodell ohne Grundplatte
- ``mit_grundplatte/mosaik.ldr``: dasselbe auf einer Grundplatte 32 × 32
- ``vorschau.png``: so soll das Modell in Studio von oben aussehen

Inhalt (12 × 8 Noppen), oben = Bildzeile 0:

- Ecken: oben links Rot, oben rechts Blau, unten links Gelb (1×1-Plates)
  → zeigt, ob das Bild gespiegelt oder gedreht ist
- Plate 1×4 schwarz, ungedreht (waagerecht) und gedreht (senkrecht)
- Plate 2×4 weiß, ungedreht (waagerecht) und gedreht (senkrecht)
"""

import dataclasses
from pathlib import Path

import numpy as np

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.layout import baseplate_placements, find_baseplate
from lego_mosaik.domain.models import Mosaic, Placement
from lego_mosaik.main import build_catalog_source
from lego_mosaik.outputs.ldraw import LdrawWriter
from lego_mosaik.outputs.preview_png import PreviewPngWriter

TARGET_DIR = Path("ausgabe/orientierung")
WIDTH, HEIGHT = 12, 8
# Rebrickable-Farb-IDs
RED, BLUE, YELLOW, BLACK, WHITE = 4, 1, 14, 0, 15
BASEPLATE_COLOR = 71  # Light Bluish Gray
BASEPLATE_STUDS = 32


def build_test_mosaic() -> Mosaic:
    """Baut das Testmosaik aus echten Teilen des Katalogs."""
    catalog = build_catalog_source(SourceConfig(data_dir=Path("daten/rebrickable"))).load_catalog()
    parts, colors = catalog.part_types, catalog.colors

    def place(part: str, color: int, row: int, col: int, *, rotated: bool = False) -> Placement:
        return Placement(parts[part], colors[color], row, col, rotated)

    placements = (
        place("3024", RED, 0, 0),
        place("3024", BLUE, 0, WIDTH - 1),
        place("3024", YELLOW, HEIGHT - 1, 0),
        place("3710", BLACK, 1, 2),  # 1×4 waagerecht: Zeile 1, Spalten 2–5
        place("3710", BLACK, 3, 2, rotated=True),  # 1×4 senkrecht: Zeilen 3–6, Spalte 2
        place("3020", WHITE, 1, 7),  # 2×4 waagerecht: Zeilen 1–2, Spalten 7–10
        place("3020", WHITE, 4, 7, rotated=True),  # 2×4 senkrecht: Zeilen 4–7, Spalten 7–8
    )
    baseplate = find_baseplate(catalog, BASEPLATE_STUDS)
    baseplates = baseplate_placements(baseplate, colors[BASEPLATE_COLOR], 1, 1)
    return Mosaic(
        title="Orientierungstest",
        width=WIDTH,
        height=HEIGHT,
        color_grid=np.zeros((HEIGHT, WIDTH), dtype=np.intp),
        palette=(),
        placements=placements,
        baseplates=baseplates,
    )


def main() -> None:
    """Schreibt die Prüfdateien."""
    with_baseplate = build_test_mosaic()
    without_baseplate = dataclasses.replace(with_baseplate, baseplates=())
    baseplate_dir = TARGET_DIR / "mit_grundplatte"
    baseplate_dir.mkdir(parents=True, exist_ok=True)
    mosaic = without_baseplate
    LdrawWriter(rows_per_step=2).write(without_baseplate, TARGET_DIR)
    LdrawWriter(rows_per_step=2).write(with_baseplate, baseplate_dir)
    PreviewPngWriter(stud_px=32, draw_part_edges=True).write(mosaic, TARGET_DIR)
    print(f"Prüfdateien geschrieben nach {TARGET_DIR.resolve()}")


if __name__ == "__main__":
    main()
