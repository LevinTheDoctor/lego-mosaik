"""Tests für die Domain-Modelle."""

from lego_mosaik.domain.models import PartCatalog, PartKind, PartsList, Placement


def test_placement_spans_follow_rotation(catalog: PartCatalog) -> None:
    plate_2x4 = catalog.part_types["3020"]
    black = catalog.colors[0]
    flat = Placement(plate_2x4, black, row=0, col=0, rotated=False)
    turned = Placement(plate_2x4, black, row=0, col=0, rotated=True)
    # ungedreht: lange Seite (4) entlang der Spalten
    assert (flat.rows_spanned, flat.cols_spanned) == (2, 4)
    assert (turned.rows_spanned, turned.cols_spanned) == (4, 2)
    assert len(list(turned.cells())) == 8


def test_parts_list_aggregates_and_sorts(catalog: PartCatalog) -> None:
    plate_1x1, plate_1x2 = catalog.part_types["3024"], catalog.part_types["3023"]
    white, black = catalog.colors[15], catalog.colors[0]
    placements = [
        Placement(plate_1x1, white, 0, 0),
        Placement(plate_1x2, black, 0, 1),
        Placement(plate_1x1, white, 1, 0),
        Placement(plate_1x1, black, 1, 1),
    ]
    parts_list = PartsList.from_placements(placements)
    assert parts_list.total_quantity == len(placements)
    rows = [(e.color.id, e.part.part_num, e.quantity) for e in parts_list.entries]
    # nach Farbfamilie (Grautöne von Weiß bis Schwarz), dann Größe
    assert rows == [(15, "3024", 2), (0, "3024", 1), (0, "3023", 1)]


def test_catalog_lists_parts_in_color(catalog: PartCatalog) -> None:
    parts = catalog.parts_in_color(15, (PartKind.PLATE,))
    assert [part.part_num for part in parts] == ["3020", "3023", "3024"]
    assert catalog.element_id("3024", 15) is not None
    assert catalog.element_id("3024", 2) is None
