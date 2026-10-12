"""Tests für BrickLink-XML und LEGO-Liste."""

import csv
import dataclasses
import logging
from pathlib import Path
from xml.etree import ElementTree

import numpy as np
import pytest

from lego_mosaik.domain.models import LegoColor, Mosaic, PartCatalog, Placement
from lego_mosaik.outputs.official_parts_lists import OfficialPartsListsWriter

BRICKLINK_CODES = {0: 11, 15: 1}  # Black, White
LEGO_NAMES = {0: "Black", 15: "White"}


def _with_codes(color: LegoColor) -> LegoColor:
    return dataclasses.replace(
        color, bricklink_code=BRICKLINK_CODES.get(color.id), lego_name=LEGO_NAMES.get(color.id)
    )


@pytest.fixture
def mosaic(catalog: PartCatalog) -> Mosaic:
    black, white = _with_codes(catalog.colors[0]), _with_codes(catalog.colors[15])
    tile, plate = catalog.part_types["3070b"], catalog.part_types["3020"]
    placements = (
        Placement(tile, black, 0, 0),
        Placement(tile, black, 0, 1),
        Placement(tile, white, 1, 0),
        Placement(plate, white, 2, 0),
    )
    return Mosaic(
        "test",
        4,
        4,
        np.zeros((4, 4), dtype=np.intp),
        (black, white),
        placements,
        element_ids=catalog.element_ids,
    )


def test_bricklink_xml(mosaic: Mosaic, tmp_path: Path) -> None:
    OfficialPartsListsWriter().write(mosaic, tmp_path)
    root = ElementTree.parse(tmp_path / "teileliste_bricklink.xml").getroot()
    items = [{child.tag: child.text for child in item} for item in root.iter("ITEM")]
    assert root.tag == "INVENTORY"
    assert items[0] == {"ITEMTYPE": "P", "ITEMID": "3070", "COLOR": "11", "MINQTY": "2"}
    assert sum(int(item["MINQTY"] or 0) for item in items) == len(mosaic.placements)


def test_missing_bricklink_codes_are_skipped_with_warning(
    mosaic: Mosaic, catalog: PartCatalog, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    red = catalog.colors[4]  # ohne BrickLink-Code
    extra = Placement(catalog.part_types["3024"], red, 3, 3)
    mosaic = dataclasses.replace(mosaic, placements=(*mosaic.placements, extra))
    with caplog.at_level(logging.WARNING):
        OfficialPartsListsWriter().write(mosaic, tmp_path)
    assert "Red" in caplog.text
    xml = (tmp_path / "teileliste_bricklink.xml").read_text(encoding="utf-8")
    assert "<ITEMID>3024</ITEMID>" not in xml
    assert (tmp_path / "teileliste_lego.csv").is_file()


def test_lego_list_uses_official_names(mosaic: Mosaic, tmp_path: Path) -> None:
    path = OfficialPartsListsWriter().write(mosaic, tmp_path)
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert rows[0]["Design-ID"] == "3070"
    assert rows[0]["LEGO-Farbe"] == "Black"
    assert rows[0]["Element-ID"] != ""
    assert sum(int(row["Anzahl"]) for row in rows) == len(mosaic.placements)
