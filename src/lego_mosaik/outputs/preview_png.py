"""Vorschau-PNG: jede Noppe als Quadrat mit angedeutetem Noppenkreis."""

import logging
from pathlib import Path

from lego_mosaik.domain.models import Mosaic
from lego_mosaik.outputs.rendering import Region, render_placements

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
        """Zeichnet die sichtbare Oberfläche (Rahmen und Bild) und speichert sie als PNG."""
        image = render_placements(
            mosaic.surface,
            region=Region(0, 0, mosaic.height, mosaic.width),
            stud_px=self._stud_px,
            draw_part_edges=self._draw_part_edges,
        )
        path = target_dir / FILE_NAME
        image.save(path, format="PNG")
        logger.info("Vorschau geschrieben: %s", path)
        return path
