"""``raster.json``: Farbe jeder Noppe, z. B. für einen Editor im Frontend."""

import json
import logging
from pathlib import Path

lazy import numpy as np

from lego_mosaik.domain.models import Mosaic

logger = logging.getLogger(__name__)

FILE_NAME = "raster.json"


class RasterJsonWriter:
    """Schreibt das Farbraster der sichtbaren Oberfläche (inklusive Rahmen).

    Zeilen und Spalten zählen wie in Vorschau und Anleitung; ``rahmen`` sagt, wie viele
    äußere Ringe zum Rahmen gehören (die lassen sich nicht bearbeiten).

    Args:
        frame_width: Rahmenbreite in Noppen.
    """

    name = "raster_json"

    def __init__(self, frame_width: int) -> None:
        self._frame_width = frame_width

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Schreibt ``raster.json`` mit Farben und Zeilen aus Farb-IDs."""
        ids = np.array([color.id for color in mosaic.palette])
        used = sorted({color.id: color for color in mosaic.palette}.values(), key=lambda c: c.id)
        content = {
            "breite": mosaic.width,
            "hoehe": mosaic.height,
            "rahmen": self._frame_width,
            "farben": [{"id": c.id, "name": c.name, "hex": c.hex} for c in used],
            # ids[grid]: jeder Palettenindex wird durch seine Farb-ID ersetzt
            "zeilen": ids[mosaic.color_grid].tolist(),
        }
        path = target_dir / FILE_NAME
        path.write_text(json.dumps(content, separators=(",", ":")) + "\n", encoding="utf-8")
        logger.info("Raster geschrieben: %s", path)
        return path
