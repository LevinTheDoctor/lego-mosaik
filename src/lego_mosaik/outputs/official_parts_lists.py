"""Offizielle Bestelllisten: BrickLink Wanted List (XML) und eine Liste für LEGO Pick a Brick.

BrickLink-Format laut BrickLink-Hilfe („Wanted List Upload“)::

    <INVENTORY>
      <ITEM>
        <ITEMTYPE>P</ITEMTYPE>    P = Part (Einzelteil)
        <ITEMID>3070</ITEMID>     BrickLink-Teilenummer (kann von Rebrickable abweichen!)
        <COLOR>11</COLOR>         BrickLink-Farbcode (nicht die Rebrickable-ID)
        <MINQTY>4</MINQTY>        gewünschte Stückzahl
        <CONDITION>N</CONDITION>  N = neu, U = gebraucht
        <MAXPRICE>0.05</MAXPRICE> optional: Höchstpreis pro Stück (nach ``lego-mosaik preise``)
      </ITEM>
    </INVENTORY>
"""

import csv
import logging
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree

from lego_mosaik.domain.models import Mosaic, OrderItem, PartsList

logger = logging.getLogger(__name__)

BRICKLINK_FILE_NAME = "teileliste_bricklink.xml"
LEGO_FILE_NAME = "teileliste_lego.csv"
ITEM_TYPE_PART = "P"
LEGO_HEADER = ("Element-ID", "Design-ID", "Teilname", "LEGO-Farbe", "Anzahl")
CSV_ENCODING = "utf-8-sig"  # BOM, damit Excel UTF-8 erkennt
CSV_DELIMITER = ";"  # deutsches Excel
XML_INDENT = "  "
DEFAULT_CONDITION = "N"  # neu

type MaxPrices = Mapping[tuple[str, int], Decimal]


class OfficialPartsListsWriter:
    """Schreibt ``teileliste_bricklink.xml`` und ``teileliste_lego.csv``.

    Args:
        condition: Zustand in der Wanted List, ``N`` (neu) oder ``U`` (gebraucht).
    """

    name = "official_parts_lists"

    def __init__(self, condition: str = DEFAULT_CONDITION) -> None:
        self._condition = condition

    def write(self, mosaic: Mosaic, target_dir: Path) -> Path:
        """Schreibt beide Listen; gibt den Pfad der LEGO-Liste zurück (die gibt es immer)."""
        parts_list = PartsList.from_placements(mosaic.all_parts)
        lego_path = target_dir / LEGO_FILE_NAME
        write_lego_list(mosaic, parts_list, lego_path)
        write_bricklink_xml(
            parts_list.order_items, target_dir / BRICKLINK_FILE_NAME, condition=self._condition
        )
        return lego_path


def write_bricklink_xml(
    items: Sequence[OrderItem],
    path: Path,
    *,
    condition: str = DEFAULT_CONDITION,
    max_prices: MaxPrices | None = None,
) -> None:
    """Schreibt die Wanted List. Farben ohne BrickLink-Code werden mit Warnung ausgelassen.

    Args:
        items: Bestellpositionen (aus der Teileliste oder aus ``ergebnis.json``).
        path: Zieldatei.
        condition: ``N`` (neu) oder ``U`` (gebraucht).
        max_prices: Höchstpreis pro Stück je (BrickLink-Teil, BrickLink-Farbe), optional.
    """
    text, written = bricklink_xml(items, condition=condition, max_prices=max_prices)
    path.write_text(text, encoding="utf-8")
    logger.info("BrickLink-Wanted-List geschrieben: %s (%d Positionen)", path, written)


def bricklink_xml(
    items: Sequence[OrderItem],
    *,
    condition: str = DEFAULT_CONDITION,
    max_prices: MaxPrices | None = None,
) -> tuple[str, int]:
    """Die Wanted List als Text und die Zahl der Positionen darin (für Datei und API)."""
    known = [item for item in items if item.bricklink_color is not None]
    skipped = sorted({item.color_name for item in items} - {i.color_name for i in known})
    if skipped:
        logger.warning(
            "BrickLink-Farbcode unbekannt (Studio-Farbtabelle fehlt?), ausgelassen: %s",
            ", ".join(skipped),
        )
    root = ElementTree.Element("INVENTORY")
    for item in known:
        _append_item(root, item, condition, (max_prices or {}).get(_key(item)))
    # indent rückt verschachtelte Elemente ein, damit die Datei lesbar bleibt
    ElementTree.indent(root, space=XML_INDENT)
    body = ElementTree.tostring(root, encoding="unicode")
    return f"<?xml version='1.0' encoding='utf-8'?>\n{body}", len(known)


def _key(item: OrderItem) -> tuple[str, int]:
    assert item.bricklink_key is not None  # nur Positionen mit Farbcode kommen hierher
    return item.bricklink_key


def _append_item(
    root: ElementTree.Element, entry: OrderItem, condition: str, max_price: Decimal | None
) -> None:
    """Hängt ein ``<ITEM>`` mit Teil, Farbe, Menge, Zustand und ggf. Höchstpreis an."""
    item = ElementTree.SubElement(root, "ITEM")
    values = [
        ("ITEMTYPE", ITEM_TYPE_PART),
        ("ITEMID", entry.bricklink_item),
        ("COLOR", str(entry.bricklink_color)),
        ("MINQTY", str(entry.quantity)),
        ("CONDITION", condition),
    ]
    if max_price is not None:
        values.append(("MAXPRICE", f"{max_price:.4f}"))
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
