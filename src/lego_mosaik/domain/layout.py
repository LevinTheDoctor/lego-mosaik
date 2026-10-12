"""Layout: Mosaikgröße in Grundplatten, Grundplatten-Platzierung und Rahmen.

Reine Funktionen auf Domain-Objekten, ohne Abhängigkeit zu Konfiguration oder I/O.
"""

from collections.abc import Sequence
from dataclasses import dataclass

lazy import numpy as np

from lego_mosaik.domain.errors import ConfigError
from lego_mosaik.domain.models import (
    IndexGrid,
    LegoColor,
    PartCatalog,
    PartKind,
    PartType,
    Placement,
)


@dataclass(frozen=True, slots=True)
class LayoutSize:
    """Abmessungen in Noppen.

    Attributes:
        width: Gesamtbreite (Grundplatten × Plattengröße).
        height: Gesamthöhe.
        image_width: Breite der Bildfläche innerhalb des Rahmens.
        image_height: Höhe der Bildfläche innerhalb des Rahmens.
    """

    width: int
    height: int
    image_width: int
    image_height: int


def layout_size(
    baseplate_studs: int, plates_wide: int, plates_high: int, frame_width: int
) -> LayoutSize:
    """Berechnet Gesamt- und Bildgröße: Bildfläche = Gesamtgröße − Rahmen auf beiden Seiten."""
    width, height = baseplate_studs * plates_wide, baseplate_studs * plates_high
    return LayoutSize(width, height, width - 2 * frame_width, height - 2 * frame_width)


def find_baseplate(catalog: PartCatalog, studs: int) -> PartType:
    """Sucht die quadratische Grundplatte mit ``studs`` × ``studs`` Noppen im Katalog.

    Raises:
        ConfigError: Wenn es keine solche Grundplatte gibt; nennt die verfügbaren Größen.
    """
    baseplates = [p for p in catalog.part_types.values() if p.kind is PartKind.BASEPLATE]
    for part in baseplates:
        if part.width_studs == part.length_studs == studs:
            return part
    sizes = ", ".join(str(size) for size in sorted({p.length_studs for p in baseplates}))
    raise ConfigError(f"Keine Grundplatte mit {studs} × {studs} Noppen. Verfügbar: {sizes}.")


def require_baseplate_color(catalog: PartCatalog, baseplate: PartType, color_id: int) -> LegoColor:
    """Prüft, dass es die Grundplatte in der Farbe gibt, und liefert die Farbe.

    Raises:
        ConfigError: Sonst, mit den verfügbaren Farben dieser Grundplatte.
    """
    color = catalog.colors.get(color_id)
    if (
        color is not None
        and color.ldraw_code is not None
        and catalog.is_available(baseplate.part_num, color_id)
    ):
        return color
    available = sorted(
        f"{c.id} {c.name}"
        for c in catalog.colors.values()
        if catalog.is_available(baseplate.part_num, c.id) and c.ldraw_code is not None
    )
    raise ConfigError(
        f"Die Grundplatte {baseplate.part_num} gibt es nicht in Farbe {color_id}. "
        f"Verfügbar: {', '.join(available)}."
    )


def require_frame_color(
    catalog: PartCatalog, color_id: int, part_kinds: Sequence[PartKind]
) -> LegoColor:
    """Prüft, dass sich der Rahmen in dieser Farbe legen lässt (1×1-Teil vorhanden).

    Raises:
        ConfigError: Wenn die Farbe unbekannt ist, keinen LDraw-Code hat oder kein
            1×1-Teil der erlaubten Teilarten in ihr existiert.
    """
    color = catalog.colors.get(color_id)
    if color is None or color.ldraw_code is None:
        raise ConfigError(f"Die Rahmenfarbe {color_id} ist unbekannt.")
    if not any(part.is_single_stud for part in catalog.parts_in_color(color_id, part_kinds)):
        kinds = ", ".join(kind.value for kind in part_kinds)
        raise ConfigError(f"Für die Rahmenfarbe {color.name} gibt es kein 1×1-Teil ({kinds}).")
    return color


def baseplate_placements(
    baseplate: PartType, color: LegoColor, plates_wide: int, plates_high: int
) -> tuple[Placement, ...]:
    """Grundplatten lückenlos nebeneinander, zeilenweise von oben links."""
    studs = baseplate.length_studs
    return tuple(
        Placement(baseplate, color, row=plate_row * studs, col=plate_col * studs)
        for plate_row in range(plates_high)
        for plate_col in range(plates_wide)
    )


def apply_frame(
    color_grid: IndexGrid, palette: Sequence[LegoColor], frame_width: int, frame_color: LegoColor
) -> tuple[IndexGrid, tuple[LegoColor, ...]]:
    """Legt einen Rahmen in ``frame_color`` um das Farbraster.

    Fehlt die Rahmenfarbe in der Palette, wird sie hinten angehängt; die
    Indizes der übrigen Farben bleiben dadurch unverändert.

    Returns:
        Das größere Raster und die (ggf. erweiterte) Palette.
    """
    if frame_width == 0:
        return color_grid, tuple(palette)
    colors = list(palette)
    if frame_color not in colors:
        colors.append(frame_color)
    frame_index = colors.index(frame_color)
    # np.pad hängt auf allen vier Seiten frame_width Zellen mit dem Rahmenindex an
    framed = np.pad(color_grid, frame_width, mode="constant", constant_values=frame_index)
    return framed.astype(np.intp), tuple(colors)
