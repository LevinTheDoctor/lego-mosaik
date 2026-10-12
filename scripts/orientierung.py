"""Erzeugt Prüfdateien für die LDraw-Ausrichtung in Bricklink Studio.

Aufruf::

    uv run python scripts/orientierung.py

Ergebnis in ``ausgabe/orientierung/``:

- ``mosaik.ldr``: Testmodell ohne Unterbau
- ``mit_grundplatte/mosaik.ldr``: dasselbe auf einer Grundplatte 32 × 32
- ``art_paneele/mosaik.ldr``: zwei LEGO-Art-Paneele mit Technic-Pins und erhöhtem Rahmen
- ``vorschau.png``: so soll das Modell in Studio von oben aussehen

Inhalt (12 × 8 Noppen), oben = Bildzeile 0:

- Ecken: oben links Rot, oben rechts Blau, unten links Gelb (1×1-Plates)
  → zeigt, ob das Bild gespiegelt oder gedreht ist
- Plate 1×4 schwarz, ungedreht (waagerecht) und gedreht (senkrecht)
- Plate 2×4 weiß, ungedreht (waagerecht) und gedreht (senkrecht)

Im Modell ``art_paneele`` bitte prüfen (die Werte sind Annahmen):

- Paneele (65803) liegen bündig unter den Plates, nicht darin oder darüber
  → sonst Ursprung von 65803.dat anders als angenommen (Oberkante, Mitte)
- Pins (2780) stecken quer in der Naht und in den Seitenlöchern
  → Achse: ``ldraw_long_axis`` in ``daten/teil_geometrie.csv``;
  Höhe: ``connector_depth_ldu`` in ``CONFIG.layout``; Lage: ``pins_per_seam``
- Der Rahmen (Tiles auf einer Lage Plates) steht eine Plate über dem Bild
"""

import dataclasses
from pathlib import Path

import numpy as np

from lego_mosaik.config import SourceConfig
from lego_mosaik.domain.layout import (
    connector_placements,
    find_connector,
    find_substrate_part,
    grid_layout_size,
    substrate_placements,
)
from lego_mosaik.domain.models import PLATE_HEIGHT_LDU, Mosaic, PartKind, Placement
from lego_mosaik.main import CONFIG, build_catalog_source
from lego_mosaik.outputs.ldraw import LdrawWriter
from lego_mosaik.outputs.preview_png import PreviewPngWriter

TARGET_DIR = Path("ausgabe/orientierung")
WIDTH, HEIGHT = 12, 8
# Rebrickable-Farb-IDs
RED, BLUE, YELLOW, BLACK, WHITE = 4, 1, 14, 0, 15
BASEPLATE_COLOR = 71  # Light Bluish Gray
BASEPLATE_STUDS = 32
PANEL_STUDS = 16


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
    baseplate = find_substrate_part(catalog, PartKind.BASEPLATE, BASEPLATE_STUDS)
    baseplates = substrate_placements(baseplate, colors[BASEPLATE_COLOR], 1, 1)
    return Mosaic(
        title="Orientierungstest",
        width=WIDTH,
        height=HEIGHT,
        color_grid=np.zeros((HEIGHT, WIDTH), dtype=np.intp),
        palette=(),
        placements=placements,
        substrate=baseplates,
    )


def build_art_mosaic(test: Mosaic) -> Mosaic:
    """Zwei Art-Paneele nebeneinander, mit Pins verbunden; oben ein erhöhter Rahmenstreifen."""
    catalog = build_catalog_source(SourceConfig(data_dir=Path("daten/rebrickable"))).load_catalog()
    parts, colors = catalog.part_types, catalog.colors
    panel = find_substrate_part(catalog, PartKind.PANEL, PANEL_STUDS)
    panels = substrate_placements(panel, colors[BLACK], plates_wide=2, plates_high=1)
    size = grid_layout_size(PANEL_STUDS, plates_wide=2, plates_high=1, frame_width=0)
    layout = CONFIG.layout
    pins = connector_placements(
        find_connector(catalog), colors[BLACK], size, PANEL_STUDS, layout.pins_per_seam
    )
    # Rahmenstreifen über der Naht (Zeile 15): Plate 1×8 als Stütze, darauf Tiles 1×8
    support = Placement(parts["3460"], colors[BLACK], 15, 12)
    frame = Placement(parts["4162"], colors[WHITE], 15, 12, bottom_ldu=PLATE_HEIGHT_LDU)
    return dataclasses.replace(
        test,
        title="Orientierungstest Art-Paneele",
        substrate=panels,
        connectors=pins,
        supports=(support,),
        frame=(frame,),
    )


def main() -> None:
    """Schreibt die Prüfdateien."""
    with_baseplate = build_test_mosaic()
    without_baseplate = dataclasses.replace(with_baseplate, substrate=())
    art = build_art_mosaic(with_baseplate)
    writer = LdrawWriter(
        rows_per_step=2,
        section_studs=WIDTH,
        connector_depth_ldu=CONFIG.layout.connector_depth_ldu,
    )
    for folder, mosaic in (
        (TARGET_DIR, without_baseplate),
        (TARGET_DIR / "mit_grundplatte", with_baseplate),
        (TARGET_DIR / "art_paneele", art),
    ):
        folder.mkdir(parents=True, exist_ok=True)
        writer.write(mosaic, folder)
    PreviewPngWriter(stud_px=32, draw_part_edges=True).write(without_baseplate, TARGET_DIR)
    print(f"Prüfdateien geschrieben nach {TARGET_DIR.resolve()}")


if __name__ == "__main__":
    main()
