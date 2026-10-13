"""``ergebnis.json``: Konfiguration, Statistik, Palette und erzeugte Dateien (für die API)."""

import json
import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import Mosaic, OrderItem, PartsList

logger = logging.getLogger(__name__)

FILE_NAME = "ergebnis.json"
TEMP_SUFFIX = ".tmp"
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
        _dump(path, result)
        logger.info("Ergebnis geschrieben: %s", path)
        return path


def update_result(
    target_dir: Path, values: Mapping[str, object], new_files: Iterable[str] = ()
) -> None:
    """Ergänzt ``ergebnis.json`` eines fertigen Mosaiks (z. B. um Preise).

    ``new_files`` kommen in die Dateiliste, damit die API sie ausliefert. Geschrieben
    wird über eine temporäre Datei, damit ein Abbruch nie eine halbe Datei hinterlässt.
    """
    path = target_dir / FILE_NAME
    result = json.loads(path.read_text(encoding="utf-8"))
    result.update(values)
    result["dateien"] = sorted(set(result.get("dateien", [])) | set(new_files))
    temp = path.with_name(path.name + TEMP_SUFFIX)
    _dump(temp, result)
    temp.replace(path)


def find_mosaic(output_dir: Path, reference: str | None) -> Path:
    """Ordner zu einer ID oder einem Pfad; ohne Angabe das neueste Mosaik.

    Raises:
        ConfigError: Wenn es kein passendes Mosaik gibt.
    """
    if reference is None:
        candidates = [p.parent for p in output_dir.glob(f"*/{FILE_NAME}")]
        if not candidates:
            raise ConfigError(f"In „{output_dir}“ gibt es noch kein Mosaik.")
        return max(candidates, key=lambda path: path.stat().st_mtime)
    for directory in (Path(reference), output_dir / reference):
        if (directory / FILE_NAME).is_file():
            return directory
    raise ConfigError(f"Mosaik „{reference}“ gibt es nicht (ID oder Ordner angeben).")


def _dump(path: Path, result: Mapping[str, object]) -> None:
    # ensure_ascii=False schreibt Umlaute direkt statt als ü
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
        "teileliste": [order_item_to_json(item) for item in parts_list.order_items],
    }


def order_item_to_json(item: OrderItem) -> dict[str, object]:
    """Eine Position der Teileliste mit deutschen Schlüsseln (wie die übrigen Felder)."""
    return {
        "teil": item.part_num,
        "bricklink_teil": item.bricklink_item,
        "name": item.name,
        "farbe_id": item.color_id,
        "farbe": item.color_name,
        "bricklink_farbe": item.bricklink_color,
        "anzahl": item.quantity,
    }


def order_item_from_json(data: Mapping[str, object]) -> OrderItem:
    """Gegenstück zu ``order_item_to_json`` (z. B. für die Preisabfrage eines alten Laufs).

    Raises:
        KeyError, TypeError, ValueError: Wenn ein Feld fehlt oder den falschen Typ hat.
    """
    color = data["bricklink_farbe"]
    return OrderItem(
        part_num=str(data["teil"]),
        bricklink_item=str(data["bricklink_teil"]),
        name=str(data["name"]),
        color_id=_int(data["farbe_id"]),
        color_name=str(data["farbe"]),
        bricklink_color=None if color is None else _int(color),
        quantity=_int(data["anzahl"]),
    )


def _int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"Ganzzahl erwartet, nicht {value!r}")
    return value
