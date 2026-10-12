"""Vorschau-PNG: jede Noppe als Quadrat mit angedeutetem Noppenkreis."""

import logging
from pathlib import Path

from lego_mosaik.domain.models import Mosaic
from lego_mosaik.outputs.rendering import EMPTY_CELL_RGB, Region, render_placements

logger = logging.getLogger(__name__)

FILE_NAME = "vorschau.png"


class PreviewPngWriter:
    """Schreibt ``vorschau.png``.

    Args:
        stud_px: Kantenlänge einer Noppe in Pixeln.
        draw_part_edges: Teilekanten als Linien einzeichnen.
    """

    name = "preview_png"

    def __init__(self, *, stud_px: int, draw_part_edges: bool) -> None:
        self._stud_px = stud_px
        self._draw_part_edges = draw_part_edges

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Zeichnet die Draufsicht und speichert sie als PNG.

        Gezeichnet wird von unten nach oben (Relief-Stützlagen, dann Rahmen und Bild).
        Der Hintergrund hat die Farbe des Unterbaus: Er ist in den Lücken der Formteile zu sehen.
        """
        background = mosaic.substrate[0].color.rgb if mosaic.substrate else EMPTY_CELL_RGB
        image = render_placements(
            mosaic.stacked,
            region=Region(0, 0, mosaic.height, mosaic.width),
            stud_px=self._stud_px,
            draw_part_edges=self._draw_part_edges,
            background=background,
        )
        path = target_dir / FILE_NAME
        image.save(path, format="PNG")
        logger.info("Vorschau geschrieben: %s", path)
        return path
