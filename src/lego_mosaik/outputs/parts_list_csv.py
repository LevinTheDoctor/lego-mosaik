"""Teilelisten als CSV: eine eigene, ausführliche Liste und eine für den Rebrickable-Import."""

import csv
import logging
from pathlib import Path

from lego_mosaik.domain.models import Mosaic, PartsList

logger = logging.getLogger(__name__)

FILE_NAME = "teileliste.csv"
REBRICKABLE_FILE_NAME = "teileliste_rebrickable.csv"
# utf-8-sig schreibt ein BOM (Byte Order Mark) an den Anfang; daran erkennt Excel UTF-8
ENCODING = "utf-8-sig"
# Deutsches Excel erwartet ";" als Trennzeichen (das Komma ist dort Dezimalzeichen);
# das Rebrickable-Importformat verlangt dagegen Kommas
GERMAN_DELIMITER = ";"
REBRICKABLE_DELIMITER = ","
HEADER = ("Teil-Nr.", "Teilname", "Farb-ID", "Farbname", "Anzahl", "Element-ID")
REBRICKABLE_HEADER = ("Part", "Color", "Quantity")


class PartsListCsvWriter:
    """Schreibt ``teileliste.csv`` und ``teileliste_rebrickable.csv``."""

    name = "parts_list_csv"

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Schreibt beide Listen und gibt den Pfad der eigenen Liste zurück."""
        parts_list = PartsList.from_placements(mosaic.all_parts)
        path = target_dir / FILE_NAME
        _write_csv(path, HEADER, _detail_rows(mosaic, parts_list), GERMAN_DELIMITER)
        _write_csv(
            target_dir / REBRICKABLE_FILE_NAME,
            REBRICKABLE_HEADER,
            _rebrickable_rows(parts_list),
            REBRICKABLE_DELIMITER,
        )
        logger.info(
            "Teilelisten geschrieben: %d Positionen, %d Teile.",
            len(parts_list.entries),
            parts_list.total_quantity,
        )
        return path


def _detail_rows(mosaic: Mosaic, parts_list: PartsList) -> list[tuple[object, ...]]:
    """Zeilen der eigenen Liste (bereits nach Farbe, dann Teil sortiert)."""
    return [
        (
            entry.part.part_num,
            entry.part.name,
            entry.color.id,
            entry.color.name,
            entry.quantity,
            # .get(..., ""): fehlt die Element-ID, bleibt die Zelle leer
            mosaic.element_ids.get((entry.part.part_num, entry.color.id), ""),
        )
        for entry in parts_list.entries
    ]


def _rebrickable_rows(parts_list: PartsList) -> list[tuple[object, ...]]:
    """Zeilen im Rebrickable-Importformat ``Part,Color,Quantity``."""
    return [(entry.part.part_num, entry.color.id, entry.quantity) for entry in parts_list.entries]


def _write_csv(
    path: Path, header: tuple[str, ...], rows: list[tuple[object, ...]], delimiter: str
) -> None:
    """Schreibt eine CSV-Datei mit Kopfzeile."""
    with path.open("w", encoding=ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(header)
        writer.writerows(rows)
