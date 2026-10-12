"""Offizielle Bestelllisten: BrickLink Wanted List (XML) und eine Liste für LEGO Pick a Brick.

BrickLink-Format laut BrickLink-Hilfe („Wanted List Upload“)::

    <INVENTORY>
      <ITEM>
        <ITEMTYPE>P</ITEMTYPE>    P = Part (Einzelteil)
        <ITEMID>3070</ITEMID>     BrickLink-Teilenummer (kann von Rebrickable abweichen!)
        <COLOR>11</COLOR>         BrickLink-Farbcode (nicht die Rebrickable-ID)
        <MINQTY>4</MINQTY>        gewünschte Stückzahl
      </ITEM>
    </INVENTORY>
"""

import csv
import logging
from pathlib import Path
from xml.etree import ElementTree

from lego_mosaik.domain.models import Mosaic, PartsList, PartsListEntry

logger = logging.getLogger(__name__)

BRICKLINK_FILE_NAME = "teileliste_bricklink.xml"
LEGO_FILE_NAME = "teileliste_lego.csv"
ITEM_TYPE_PART = "P"
LEGO_HEADER = ("Element-ID", "Design-ID", "Teilname", "LEGO-Farbe", "Anzahl")
CSV_ENCODING = "utf-8-sig"  # BOM, damit Excel UTF-8 erkennt
CSV_DELIMITER = ";"  # deutsches Excel
XML_INDENT = "  "


class OfficialPartsListsWriter:
    """Schreibt ``teileliste_bricklink.xml`` und ``teileliste_lego.csv``."""

    name = "official_parts_lists"

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Schreibt beide Listen; gibt den Pfad der LEGO-Liste zurück (die gibt es immer)."""
        parts_list = PartsList.from_placements(mosaic.all_parts)
        lego_path = target_dir / LEGO_FILE_NAME
        write_lego_list(mosaic, parts_list, lego_path)
        write_bricklink_xml(parts_list, target_dir / BRICKLINK_FILE_NAME)
        return lego_path


def write_bricklink_xml(parts_list: PartsList, path: Path) -> None:
    """Schreibt die Wanted List. Farben ohne BrickLink-Code werden mit Warnung ausgelassen."""
    known = [entry for entry in parts_list.entries if entry.color.bricklink_code is not None]
    skipped = sorted(
        {entry.color.name for entry in parts_list.entries} - {e.color.name for e in known}
    )
    if skipped:
        logger.warning(
            "BrickLink-Farbcode unbekannt (Studio-Farbtabelle fehlt?), ausgelassen: %s",
            ", ".join(skipped),
        )
    root = ElementTree.Element("INVENTORY")
    for entry in known:
        _append_item(root, entry)
    # indent rückt verschachtelte Elemente ein, damit die Datei lesbar bleibt
    ElementTree.indent(root, space=XML_INDENT)
    ElementTree.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    logger.info("BrickLink-Wanted-List geschrieben: %s (%d Positionen)", path, len(known))


def _append_item(root: ElementTree.Element, entry: PartsListEntry) -> None:
    """Hängt ein ``<ITEM>`` mit Teil, Farbe und Menge an."""
    item = ElementTree.SubElement(root, "ITEM")
    values = (
        ("ITEMTYPE", ITEM_TYPE_PART),
        ("ITEMID", entry.part.bricklink_item),
        ("COLOR", str(entry.color.bricklink_code)),
        ("MINQTY", str(entry.quantity)),
    )
    for tag, text in values:
        ElementTree.SubElement(item, tag).text = text


def write_lego_list(mosaic: Mosaic, parts_list: PartsList, path: Path) -> None:
    """Liste mit LEGO-Element-IDs und offiziellen Farbnamen (für LEGO Pick a Brick)."""
    with path.open("w", encoding=CSV_ENCODING, newline="") as handle:
        writer = csv.writer(handle, delimiter=CSV_DELIMITER)
        writer.writerow(LEGO_HEADER)
        for entry in parts_list.entries:
            writer.writerow(
                (
                    mosaic.element_ids.get((entry.part.part_num, entry.color.id), ""),
                    entry.part.bricklink_item,  # Design-ID entspricht meist der BrickLink-Nummer
                    entry.part.name,
                    entry.color.lego_name or entry.color.name,  # Fallback: Rebrickable-Name
                    entry.quantity,
                )
            )
    logger.info("LEGO-Teileliste geschrieben: %s", path)
