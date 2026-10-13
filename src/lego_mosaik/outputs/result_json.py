"""``ergebnis.json``: Konfiguration, Statistik, Palette und erzeugte Dateien (für die API)."""

import json
import logging
import re
from collections import Counter
from pathlib import Path

from lego_mosaik.domain.models import Mosaic, PartsList

logger = logging.getLogger(__name__)

FILE_NAME = "ergebnis.json"
INSTRUCTION_PAGE = re.compile(r"anleitung_\d+\.png")


class ResultJsonWriter:
    """Schreibt ``ergebnis.json``.

    Die Dateiliste enthält alle Dateien, die zum Zeitpunkt des Schreibens im
    Zielordner liegen. Deshalb läuft dieser Writer als letzter (siehe Pipeline).
    """

    name = "result_json"

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Sammelt alle Angaben und schreibt sie als formatiertes JSON."""
        path = target_dir / FILE_NAME
        files = sorted({file.name for file in target_dir.iterdir() if file.is_file()} | {FILE_NAME})
        result = build_result(mosaic) | {"dateien": files}  # | verbindet zwei dicts (Python 3.9+)
        statistik = result.get("statistik")
        if isinstance(statistik, dict):  # Seiten der Anleitung (0 ohne Anleitung)
            pages = sum(1 for name in files if INSTRUCTION_PAGE.fullmatch(name))
            result["statistik"] = statistik | {"seiten": pages}
        # ensure_ascii=False schreibt Umlaute direkt statt als ü
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        logger.info("Ergebnis geschrieben: %s", path)
        return path


def build_result(mosaic: Mosaic) -> dict[str, object]:
    """Inhalt von ``ergebnis.json`` ohne Dateiliste (JSON-fähige Werte)."""
    # Counter über die sichtbare Oberfläche: wie viele Noppen hat jede Farbe?
    studs_per_color = Counter[int]()
    for placement in mosaic.surface:
        studs_per_color[placement.color.id] += placement.part.area
    parts_list = PartsList.from_placements(mosaic.all_parts)
    return {
        "titel": mosaic.title,
        "breite": mosaic.width,
        "hoehe": mosaic.height,
        **mosaic.metadata,  # ** entpackt das dict: Konfiguration, Statistik, …
        "anzahl_teile": parts_list.total_quantity,
        "palette": [
            {
                "id": color.id,
                "name": color.name,
                "hex": color.hex,
                "ldraw_code": color.ldraw_code,
                "noppen": studs_per_color[color.id],
            }
            for color in mosaic.used_colors()
        ],
    }
