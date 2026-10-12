"""Gemeinsame Bausteine der Zerlegungsstrategien."""

from collections.abc import Sequence

from lego_mosaik.domain.errors import TilingError
from lego_mosaik.domain.models import LegoColor, PartCatalog, PartKind, PartType


def validate_uniform_height(catalog: PartCatalog, part_kinds: Sequence[PartKind]) -> None:
    """In der Draufsicht dürfen nur Teilarten gleicher Höhe gemischt werden.

    Plates und Tiles (8 LDU) passen zusammen, Bricks (24 LDU) nur allein. Die Höhen
    stammen aus der Geometrie-Tabelle, nicht aus dem Code.

    Raises:
        TilingError: Wenn die gewählten Teilarten unterschiedlich hoch sind.
    """
    # dict-Comprehension: pro Teilart die Menge der vorkommenden Höhen
    heights = {
        kind: {part.height_ldu for part in catalog.part_types.values() if part.kind is kind}
        for kind in part_kinds
    }
    # set().union(*mengen) vereinigt alle Mengen; mehr als ein Wert = unterschiedliche Höhen
    if len(set().union(*heights.values())) > 1:
        details = ", ".join(
            f"{kind.value}: {'/'.join(map(str, sorted(values)))} LDU"
            for kind, values in heights.items()
        )
        raise TilingError(
            "In der Draufsicht dürfen nur Teilarten gleicher Höhe gemischt werden "
            f"({details}). Bitte z. B. nur „plate,tile“ oder nur „brick“ wählen."
        )


def single_stud_part(
    catalog: PartCatalog, color: LegoColor, part_kinds: Sequence[PartKind]
) -> PartType:
    """Das 1×1-Teil für eine Farbe, nach Priorität der Teilarten (Reihenfolge in ``part_kinds``).

    Raises:
        TilingError: Wenn es in der Farbe kein 1×1-Teil gibt (sollte die Palette verhindern).
    """
    for kind in part_kinds:
        for part in catalog.parts_in_color(color.id, (kind,)):
            if part.is_single_stud:
                return part
    raise TilingError(f"Für die Farbe {color.name} gibt es kein 1×1-Teil der erlaubten Teilarten.")
